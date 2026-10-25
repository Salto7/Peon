"""Agent-to-agent messaging, idle and wake.

Model (see also ``AgentMessage``):

* Every planned agent exists from the moment the plan is accepted. An agent
  whose objective still waits on dependencies is parked ``IDLE`` instead of
  being polled; an agent can also park itself mid-work (``go_idle``).
* Messages are A2A ``Message`` objects (official ``a2a-sdk`` types, so the
  payload is protocol-compatible) stored in ``AgentMessage`` rows.
* Delivering a message to an ``IDLE`` agent wakes it: the job is moved back to
  ``PENDING`` and enqueued; on start it drains its inbox into its brief and, if
  it ran before, resumes from its checkpoint.
* When a dependency's agent finishes, a *handoff* message (its result summary)
  is delivered to every dependent agent and the ones whose dependencies are now
  all met are woken — event-driven, no polling.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Iterable
from typing import Any

from a2a.types import DataPart, Message, Part, Role, TaskState, TextPart
from django.db import transaction
from django.utils import timezone as dj_tz

from peon.projects.models import (
    TERMINAL_JOB_STATUSES,
    AgentMessage,
    Job,
    JobLifecycle,
    JobStatus,
    Objective,
    ObjectiveStatus,
    Project,
)

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 8_000
MAX_INBOX_RENDER = 12
HANDOFF_EXCERPT = 1_800

# Job lifecycle expressed in A2A task states.
_STATE = {
    JobStatus.PENDING: TaskState.submitted,
    JobStatus.RUNNING: TaskState.working,
    JobStatus.IDLE: TaskState.input_required,
    JobStatus.PAUSED: TaskState.input_required,
    JobStatus.COMPLETED: TaskState.completed,
    JobStatus.FAILED: TaskState.failed,
    JobStatus.CANCELLED: TaskState.canceled,
}


def a2a_state(status: str) -> str:
    """A2A ``TaskState`` value for a job status."""
    return _STATE.get(status, TaskState.unknown).value  # type: ignore[arg-type]


# ── message construction / rendering ────────────────────────────────────


def build_message(
    text: str,
    *,
    sender_is_agent: bool = True,
    context_id: str = "",
    task_id: str = "",
    kind: str = "inform",
    data: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A validated A2A ``Message`` as a JSON-able dict."""
    parts = [Part(root=TextPart(text=(text or "").strip()[:MAX_MESSAGE_CHARS]))]
    if data:
        parts.append(Part(root=DataPart(data=data)))
    msg = Message(
        message_id=str(uuid.uuid4()),
        role=Role.agent if sender_is_agent else Role.user,
        parts=parts,
        context_id=context_id or None,
        task_id=task_id or None,
        metadata={"kind": kind, **(metadata or {})},
    )
    return msg.model_dump(mode="json", exclude_none=True, by_alias=True)


def short_id(value: Any) -> str:
    """First 8 hex chars of an agent (job) id — the form shown to people."""
    return str(value or "").replace("-", "")[:8]


def agent_label(job: Job | None, *, fallback: str = "system") -> str:
    """``role (id8)`` for an agent; the fallback name for a non-agent sender."""
    if job is None:
        return fallback
    role = ((job.role_ids or [""])[0] or job.title or "agent").strip()
    return f"{role} ({short_id(job.pk)})"


def sender_label_of(message: AgentMessage) -> str:
    """Who sent a stored message, with the sender's agent id when it was an agent."""
    job = message.from_job if message.from_job_id else None
    if job is not None:
        return agent_label(job)
    return message.sender or "system"


def render_messages(messages: Iterable[AgentMessage]) -> str:
    """Prompt block for an agent that is picking up (or resuming) work."""
    rows = list(messages)[:MAX_INBOX_RENDER]
    if not rows:
        return ""
    blocks = []
    for m in rows:
        label = {
            AgentMessage.Kind.HANDOFF: "HANDOFF",
            AgentMessage.Kind.REQUEST: "REQUEST",
            AgentMessage.Kind.REPLY: "REPLY",
            AgentMessage.Kind.OPERATOR: "OPERATOR",
        }.get(m.kind, "MESSAGE")
        blocks.append(f"[{label} from {sender_label_of(m)}]\n{m.text}")
    return (
        "## Messages from other agents\n"
        "You were woken (or resumed) with these messages. Act on them under "
        "Rules of Engagement; reply with `send_message` if the sender needs an "
        "answer.\n\n" + "\n\n".join(blocks)
    )


# ── plumbing helpers ─────────────────────────────────────────────────────


def _alias(project_id: Any) -> str:
    from peon.projects.db import ensure_project_database

    return ensure_project_database(project_id)


def _emit(job: Job, message_type: str, content: str, **meta: Any) -> None:
    try:
        from peon.projects.job_claim import emit

        emit(job, message_type, content, **meta)
    except Exception:  # noqa: BLE001 — never fail a wake over a log line
        logger.debug("a2a emit failed", exc_info=True)


def _enqueue(job: Job) -> None:
    try:
        from peon.projects.tasks import enqueue_job

        enqueue_job(str(job.id))
    except Exception as exc:  # noqa: BLE001
        # The poll worker still claims PENDING rows when Dramatiq is off.
        logger.warning("a2a enqueue failed for %s: %s", job.id, exc)


def _reopen_project(project_id: Any) -> None:
    from peon.projects.models import FINISHED_PROJECT_STATUSES, ProjectStatus

    project = Project.objects.using("default").filter(pk=project_id).first()
    if project is not None and project.status in FINISHED_PROJECT_STATUSES:
        project.status = ProjectStatus.ACTIVE
        project.completed_at = None
        project.save(using="default", update_fields=["status", "completed_at", "updated_at"])


# ── idle / wake ──────────────────────────────────────────────────────────


def dependencies_met(job: Job) -> bool:
    if not job.objective_id or job.objective is None:
        return True
    from peon.projects.objectives import ObjectiveScheduler

    return ObjectiveScheduler().dependencies_met(job.objective)


def can_wake(job: Job) -> bool:
    """A job parked on dependencies must not start before they are met."""
    if job.status != JobStatus.IDLE:
        return False
    if job.idle_reason == "dependencies":
        return dependencies_met(job)
    return True


def park_idle(job: Job, *, reason: str, note: str = "", checkpoint: bool = False) -> Job:
    """Move a claimed/running job to IDLE (no polling; wakes on a message)."""
    from peon.projects.db import using_project

    alias = _alias(job.project_id)
    with using_project(job.project_id):
        updated = Job.objects.using(alias).filter(
            pk=job.pk, status__in=[JobStatus.RUNNING, JobStatus.PENDING]
        ).update(
            status=JobStatus.IDLE,
            idle_reason=reason[:32],
            idle_note=(note or "")[:255],
            error="",
            updated_at=dj_tz.now(),
        )
        job.refresh_from_db(using=alias)
    if updated and checkpoint:
        try:
            from peon.projects.checkpoints import write_job_checkpoint

            write_job_checkpoint(job, reason="idle")
        except Exception:  # noqa: BLE001
            logger.warning("idle checkpoint failed for %s", job.id, exc_info=True)
    if updated:
        waiting = f" — {note}" if note else ""
        _emit(
            job,
            "status",
            f"Idle ({'waiting on dependencies' if reason == 'dependencies' else 'waiting for a message'}){waiting}",
            job_status=JobStatus.IDLE,
            tag="a2a",
            event="a2a_idle",
        )
    return job


def wake_job(job: Job, *, reason: str) -> bool:
    """IDLE → PENDING and enqueue. Returns True only for the call that woke it."""
    from peon.projects.db import using_project

    alias = _alias(job.project_id)
    resume = job.started_at is not None  # ran before ⇒ a checkpoint exists
    with using_project(job.project_id):
        updated = Job.objects.using(alias).filter(
            pk=job.pk, status=JobStatus.IDLE
        ).update(
            status=JobStatus.PENDING,
            idle_reason="",
            idle_note="",
            resume_from_checkpoint=resume,
            updated_at=dj_tz.now(),
        )
        if not updated:
            return False
        job.refresh_from_db(using=alias)
    if job.project_id:
        _reopen_project(job.project_id)
    _emit(
        job,
        "status",
        f"Woken: {reason}",
        job_status=JobStatus.PENDING,
        tag="a2a",
        event="a2a_wake",
    )
    _enqueue(job)
    return True


# ── send / receive ───────────────────────────────────────────────────────


#: FAILED/CANCELLED are a genuine stop; a stray message must not resurrect them.
#: COMPLETED is not final in the same sense — it means "had nothing left to do",
#: and a new message is exactly a reason to pick back up (see Claude's agent
#: model: sending to a finished agent resumes it from its transcript).
_RESUMABLE_ON_MESSAGE = frozenset({JobStatus.COMPLETED})


def resume_completed_job(job: Job) -> Job:
    """Requeue a COMPLETED job from its checkpoint so a new message reaches it.

    Reuses ``lifecycle.queue_job`` (objective reopen + project reconcile are
    already solved there) and asks for the prior checkpoint explicitly, so the
    agent resumes with its earlier context instead of starting cold.
    """
    from peon.projects import lifecycle
    from peon.projects.db import using_project

    alias = _alias(job.project_id)
    with using_project(job.project_id):
        job.resume_from_checkpoint = True
        job.save(using=alias, update_fields=["resume_from_checkpoint"])
    return lifecycle.queue_job(job)


def _messages_between(from_job: Job, to_job: Job, alias: str) -> int:
    return (
        AgentMessage.objects.using(alias)
        .filter(from_job=from_job, to_job=to_job)
        .count()
    )


def _message_cap_exceeded(from_job: Job, to_job: Job, alias: str) -> int | None:
    """The configured cap, if this pair has reached it; else None (no guard, or room left)."""
    try:
        from peon.projects.runtime_settings import PeonSettings

        cap = PeonSettings.get_int("A2A_MAX_MESSAGES_PER_PAIR", 40)
    except Exception:  # noqa: BLE001
        cap = 40
    if cap <= 0:
        return None
    return cap if _messages_between(from_job, to_job, alias) >= cap else None


def send_message(
    project: Project,
    *,
    to_job: Job,
    text: str,
    from_job: Job | None = None,
    sender: str = "",
    kind: str = AgentMessage.Kind.INFORM,
    data: dict[str, Any] | None = None,
    wake: bool = True,
    reply_to: AgentMessage | None = None,
) -> AgentMessage:
    """Deliver an A2A message to ``to_job``; wake or resume it as needed."""
    body = (text or "").strip()
    if not body:
        raise ValueError("Message text is required")
    resurrecting = to_job.status in TERMINAL_JOB_STATUSES
    if resurrecting and to_job.status not in _RESUMABLE_ON_MESSAGE:
        raise ValueError(
            f"Agent {to_job.title!r} {to_job.status} with an error and cannot be "
            "resumed by messaging it; spawn new work instead."
        )
    if from_job is not None and from_job.pk == to_job.pk:
        raise ValueError("An agent cannot message itself")
    if kind not in {k.value for k in AgentMessage.Kind}:
        kind = AgentMessage.Kind.INFORM
    sender_label = (
        sender
        or (((from_job.role_ids or [""])[0] if from_job else "") or "")
        or "system"
    )
    alias = _alias(project.pk)
    if from_job is not None:
        cap = _message_cap_exceeded(from_job, to_job, alias)
        if cap is not None:
            raise ValueError(
                f"message cap reached ({cap}) between these two agents — this "
                "looks like a loop; stop messaging and finish your turn, or "
                "escalate via go_idle/the governance PM instead"
            )
    if reply_to is not None and (
        reply_to.to_job_id != (from_job.pk if from_job else None)
    ):
        raise ValueError("reply_to must be a message you received")
    ctx = str(to_job.objective_id or project.pk)
    msg = build_message(
        body,
        sender_is_agent=from_job is not None or kind != AgentMessage.Kind.OPERATOR,
        context_id=ctx,
        task_id=str(to_job.pk),
        kind=str(kind),
        data=data,
        metadata={
            "from_job_id": str(from_job.pk) if from_job else "",
            "sender": sender_label,
            "reply_to": str(reply_to.pk) if reply_to else "",
        },
    )
    row = AgentMessage.objects.using(alias).create(
        project=project,
        objective=to_job.objective,
        from_job=from_job,
        to_job=to_job,
        sender=sender_label[:96],
        kind=kind,
        message=msg,
        reply_to=reply_to,
    )
    # Every message is directed at exactly one agent; show both ends by id.
    from_text = agent_label(from_job, fallback=sender_label)
    to_text = agent_label(to_job)
    _emit(
        to_job,
        "log",
        f"{from_text} → {to_text} [{kind}]: {body[:400]}",
        tag="a2a",
        event="a2a_message",
        a2a_kind=str(kind),
        a2a_from=sender_label,
        a2a_to=to_text,
        a2a_from_id=str(from_job.pk) if from_job else "",
        a2a_to_id=str(to_job.pk),
        from_job_id=str(from_job.pk) if from_job else "",
        to_job_id=str(to_job.pk),
    )
    if resurrecting:
        # A COMPLETED agent has no IDLE state to wake from; requeue it from its
        # checkpoint so this message is waiting in its inbox when it starts.
        resume_completed_job(to_job)
        return row
    if not wake:
        # Coalescing backstop: wake=False messages (interim handoffs, fan-out
        # informs) accumulate cost-free, but once enough pile up on an idle
        # consumer we wake it anyway so streamed work is not stranded until the
        # producer's objective completes. The governor (escalation-only) and
        # dependency standbys (scheduler-owned) are exempt.
        wake = _interim_backstop_reached(to_job, alias)
    if wake:
        to_job.refresh_from_db()
        if to_job.status == JobStatus.IDLE and can_wake(to_job):
            wake_job(to_job, reason=f"message from {sender_label}")
    return row


def _interim_backstop_reached(to_job: Job, alias: str) -> bool:
    if to_job.profile == GOVERNANCE_PROFILE or to_job.idle_reason == "dependencies":
        return False
    to_job.refresh_from_db()
    if to_job.status != JobStatus.IDLE or not can_wake(to_job):
        return False
    from peon.projects.runtime_settings import PeonSettings

    threshold = PeonSettings.get_int("A2A_INTERIM_WAKE_UNREAD", 3)
    if threshold <= 0:
        return False
    unread_n = (
        AgentMessage.objects.using(alias)
        .filter(to_job=to_job, delivered_at__isnull=True)
        .count()
    )
    return unread_n >= threshold


def sent_message_status(from_job: Job, *, limit: int = 10) -> list[dict[str, Any]]:
    """Delivery/read state of this agent's own recent sends — the sender's feedback.

    A row exists the instant ``send_message`` returns (delivery is certain: the
    recipient is a live agent in this project, or was just resurrected). What a
    sender cannot see without asking is whether it has been *read* yet
    (``delivered_at`` is set once the recipient's inbox actually drains it) and
    whether a reply has come back.
    """
    alias = _alias(from_job.project_id)
    rows = (
        AgentMessage.objects.using(alias)
        .filter(from_job=from_job)
        .select_related("to_job")
        .order_by("-created_at")[: max(1, min(50, limit))]
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        reply = (
            AgentMessage.objects.using(alias)
            .filter(reply_to=row)
            .order_by("created_at")
            .first()
        )
        out.append(
            {
                "message_id": str(row.pk),
                "to": agent_label(row.to_job),
                "sent_at": row.created_at,
                "read": row.delivered_at is not None,
                "replied": reply is not None,
                "reply_text": reply.text[:300] if reply else "",
            }
        )
    return out


def unread(job: Job) -> list[AgentMessage]:
    alias = _alias(job.project_id)
    return list(
        AgentMessage.objects.using(alias)
        .filter(to_job=job, delivered_at__isnull=True)
        .order_by("created_at")
    )


def drain_inbox(job: Job) -> list[AgentMessage]:
    """Return and mark delivered every unread message for ``job``."""
    from peon.projects.db import using_project

    alias = _alias(job.project_id)
    with using_project(job.project_id), transaction.atomic(using=alias):
        rows = list(
            AgentMessage.objects.using(alias)
            .select_for_update()
            .filter(to_job=job, delivered_at__isnull=True)
            .order_by("created_at")
        )
        if rows:
            now = dj_tz.now()
            for r in rows:
                r.delivered_at = now
            AgentMessage.objects.using(alias).bulk_update(rows, ["delivered_at"])
    return rows


# ── handoffs & standby maintenance ───────────────────────────────────────


def _latest_job(objective: Objective, alias: str) -> Job | None:
    """The objective's own (root) agent, not a sub-agent it spawned."""
    return (
        Job.objects.using(alias)
        .filter(objective=objective, status=JobStatus.COMPLETED, parent__isnull=True)
        .order_by("-completed_at", "-created_at")
        .first()
    )


# Output of the deterministic manager bookend carries no information.
_BOOKEND_NOISE = "Manager bookend: dispatch next ready objective"


def _result_excerpt(job: Job, limit: int) -> str:
    text = (job.result or "").strip()
    return "" if _BOOKEND_NOISE in text else text[:limit]


def handoff_text(dep: Objective, job: Job | None, findings: int) -> str:
    who = ((job.role_ids or [""])[0] if job else "") or dep.role_id or "agent"
    lines = [
        f'Handoff from OBJ-{dep.seq} "{dep.title}" ({who}) — completed.',
    ]
    result = _result_excerpt(job, HANDOFF_EXCERPT) if job else ""
    if result:
        lines.append("Result:\n" + result)
    if job is not None:
        for child in job.children.filter(status=JobStatus.COMPLETED).order_by("created_at")[:5]:
            part = _result_excerpt(child, HANDOFF_EXCERPT // 2)
            if part:
                role = (child.role_ids or [""])[0] or child.title
                lines.append(f"Sub-agent {role} — {child.title}:\n{part}")
    if findings:
        lines.append(
            f"{findings} finding(s) were recorded for that objective — "
            "use `list_findings` for details."
        )
    lines.append(
        "Use `list_workspace_artifacts` / `read_workspace_artifact` for its files."
    )
    return "\n\n".join(lines)


def deliver_handoffs(objective: Objective) -> int:
    """Send each completed dependency's result to this objective's idle agents."""
    from peon.projects.db import using_project

    alias = _alias(objective.project_id)
    sent = 0
    with using_project(objective.project_id):
        idle = list(
            Job.objects.using(alias).filter(
                objective=objective, status=JobStatus.IDLE, parent__isnull=True
            )
        )
        if not idle:
            return 0
        deps = list(objective.depends_on.all())
        project = Project.objects.using("default").get(pk=objective.project_id)
        for dep in deps:
            if dep.status != ObjectiveStatus.COMPLETED:
                continue
            done = _latest_job(dep, alias)
            findings = dep.findings.count() if hasattr(dep, "findings") else 0
            text = handoff_text(dep, done, findings)
            for target in idle:
                if (
                    done is not None
                    and AgentMessage.objects.using(alias)
                    .filter(
                        to_job=target, from_job=done, kind=AgentMessage.Kind.HANDOFF
                    )
                    .exists()
                ):
                    continue
                send_message(
                    project,
                    to_job=target,
                    text=text,
                    from_job=done,
                    sender=(done.role_ids[0] if done and done.role_ids else dep.role_id),
                    kind=AgentMessage.Kind.HANDOFF,
                    data={
                        "from_objective_seq": dep.seq,
                        "from_job_id": str(done.pk) if done else "",
                        "findings": findings,
                    },
                    wake=False,  # woken once, by the scheduler, when all deps are met
                )
                sent += 1
    return sent


def _parent_handoff_text(child: Job) -> str:
    who = ((child.role_ids or [""])[0] if child.role_ids else "") or child.title or "sub-agent"
    verb = {
        JobStatus.COMPLETED: "completed",
        JobStatus.FAILED: "failed",
        JobStatus.CANCELLED: "was cancelled",
    }.get(child.status, child.status)
    lines = [f"Sub-agent {who} ({child.title}) {verb}."]
    if child.status == JobStatus.FAILED and (child.error or "").strip():
        lines.append("Error:\n" + child.error.strip()[:HANDOFF_EXCERPT])
    result = _result_excerpt(child, HANDOFF_EXCERPT)
    if result:
        lines.append("Result:\n" + result)
    lines.append(
        "Use `list_findings` / `list_workspace_artifacts` for its findings and files."
    )
    return "\n\n".join(lines)


def deliver_parent_handoff(child: Job) -> bool:
    """Push a terminal child's result to its parent, waking it only if parked.

    The symmetric twin of :func:`deliver_handoffs` for the parent/child edge:
    ``deliver_handoffs`` covers objective dependencies, this covers spawn fan-out.
    A running parent is not woken (``send_message`` wakes only an idle recipient);
    a terminal parent is skipped. Deduped to one handoff per child.
    """
    if not child.parent_id or not child.project_id:
        return False
    from peon.projects.db import using_project

    alias = _alias(child.project_id)
    with using_project(child.project_id):
        parent = Job.objects.using(alias).filter(pk=child.parent_id).first()
        if parent is None or parent.status in TERMINAL_JOB_STATUSES:
            return False
        if (
            AgentMessage.objects.using(alias)
            .filter(to_job=parent, from_job=child, kind=AgentMessage.Kind.HANDOFF)
            .exists()
        ):
            return False
        project = Project.objects.using("default").get(pk=child.project_id)
    send_message(
        project,
        to_job=parent,
        text=_parent_handoff_text(child),
        from_job=child,
        kind=AgentMessage.Kind.HANDOFF,
        wake=True,  # send_message wakes only if the parent is idle & wakeable
    )
    return True


# ── resident governance PM ────────────────────────────────────────────────
#
# One per project, born IDLE, not tied to a middle objective. It costs nothing
# while parked (event-driven, no poll/LLM), receives spawn/finding informs
# (wake=False) and wakes only on escalations. Identified durably by
# ``profile == GOVERNANCE_PROFILE`` (survives wake, unlike ``idle_reason``).

GOVERNANCE_PROFILE = "governance"


def governance_pm(project: Project) -> Job | None:
    """The live resident governance PM for ``project`` (or None)."""
    alias = _alias(project.pk)
    return (
        Job.objects.using(alias)
        .filter(project_id=project.pk, profile=GOVERNANCE_PROFILE)
        .exclude(status__in=TERMINAL_JOB_STATUSES)
        .order_by("created_at")
        .first()
    )


def ensure_governance_pm(project: Project) -> Job | None:
    """Create the single resident IDLE governance PM if none is live."""
    from orchestrator.crew.roles.registry import RoleRegistry
    from peon.projects.db import ensure_project_database, using_project

    manager_id, _ = RoleRegistry.shared().engagement_bookends()
    if not manager_id or governance_pm(project) is not None:
        return governance_pm(project)
    alias = ensure_project_database(project.pk)
    with using_project(project.pk):
        return Job.objects.using(alias).create(
            title="Governance PM",
            description=(
                "Resident engagement governor. You receive spawn and progress "
                "informs from the crew and wake only when an agent escalates a "
                "block or a scope/plan change. On wake, read your whole inbox, "
                "govern (redirect, reprioritize, update objectives, replan, or "
                "ask_operator), then end your turn — you park again automatically."
            ),
            lifecycle=JobLifecycle.LONG,
            status=JobStatus.IDLE,
            role_ids=[manager_id],
            project_id=project.pk,
            profile=GOVERNANCE_PROFILE,
            idle_reason="governance",
            idle_note="governing engagement",
            workspace_id=str(project.pk),
        )


def notify_governor(
    project: Project,
    *,
    text: str,
    from_job: Job | None = None,
    escalate: bool = False,
) -> AgentMessage | None:
    """Inform (wake=False) or escalate (wake=True) the resident governance PM."""
    pm = governance_pm(project)
    if pm is None or (from_job is not None and pm.pk == from_job.pk):
        return None
    return send_message(
        project,
        to_job=pm,
        text=text,
        from_job=from_job,
        kind=AgentMessage.Kind.REQUEST if escalate else AgentMessage.Kind.INFORM,
        wake=escalate,
    )


def try_notify_governor(
    project: Project,
    *,
    text: str,
    from_job: Job | None = None,
    escalate: bool = False,
) -> None:
    """Best-effort :func:`notify_governor` — governance must never break the caller."""
    try:
        notify_governor(project, text=text, from_job=from_job, escalate=escalate)
    except Exception:
        logger.debug("governor notify failed", exc_info=True)


def repark_governor(job: Job) -> Job:
    """Re-park the governance PM IDLE after a turn; re-wake if mail arrived."""
    park_idle(job, reason="governance", note="governing engagement", checkpoint=True)
    job.refresh_from_db()
    if job.status == JobStatus.IDLE and unread(job) and can_wake(job):
        wake_job(job, reason="escalation arrived while re-parking")
        job.refresh_from_db()
    return job


def cancel_governor(project: Project) -> int:
    """Retire the resident governance PM when the engagement finishes."""
    from peon.projects.db import using_project

    alias = _alias(project.pk)
    with using_project(project.pk):
        return Job.objects.using(alias).filter(
            project_id=project.pk,
            profile=GOVERNANCE_PROFILE,
            status=JobStatus.IDLE,
        ).update(
            status=JobStatus.CANCELLED,
            error="Engagement finished",
            completed_at=dj_tz.now(),
            idle_reason="",
            updated_at=dj_tz.now(),
        )


def sweep_orphan_standby(project: Project) -> int:
    """Cancel idle agents whose objective vanished, finished or was cancelled."""
    from peon.projects.db import using_project

    alias = _alias(project.pk)
    n = 0
    with using_project(project.pk):
        qs = Job.objects.using(alias).filter(
            project_id=project.pk, status=JobStatus.IDLE, parent__isnull=True
        ).exclude(profile=GOVERNANCE_PROFILE)
        for job in qs.select_related("objective"):
            obj = job.objective
            if obj is None or obj.status in {
                ObjectiveStatus.CANCELLED,
                ObjectiveStatus.COMPLETED,
            }:
                Job.objects.using(alias).filter(pk=job.pk, status=JobStatus.IDLE).update(
                    status=JobStatus.CANCELLED,
                    error="Superseded: objective no longer open",
                    completed_at=dj_tz.now(),
                    idle_reason="",
                    updated_at=dj_tz.now(),
                )
                n += 1
    return n


# ── addressing ───────────────────────────────────────────────────────────


def resolve_recipient(
    project: Project, spec: str, *, sender: Job | None = None
) -> tuple[Job | None, str]:
    """Find the one agent a message is addressed to.

    Messages are directed, never broadcast: ``spec`` must be an agent id — the
    full id or a unique prefix of at least 8 characters, as shown by
    ``list_team``. A role id or objective names a *group*, so it is never
    delivered; the reason lists the matching agents' ids instead.
    """
    text = (spec or "").strip().lower()
    if not text:
        return None, "recipient is required: the agent id from list_team"
    alias = _alias(project.pk)
    agents = list(Job.objects.using(alias).filter(project_id=project.pk).select_related("objective"))

    compact = text.replace("-", "")
    if re.fullmatch(r"[0-9a-f]{8,32}", compact):
        hits = [j for j in agents if str(j.pk).replace("-", "").startswith(compact)]
        if len(hits) == 1:
            return hits[0], ""
        if not hits:
            return None, f"no agent with id {text} on this project"
        listing = "; ".join(f"{j.pk} ({agent_label(j)})" for j in hits[:6])
        return None, f"id prefix {text!r} matches several agents — use more characters: {listing}"

    live = [
        j
        for j in agents
        if j.status not in TERMINAL_JOB_STATUSES and (sender is None or j.pk != sender.pk)
    ]
    token = text.removeprefix("obj-").removeprefix("obj")
    matches = [
        j
        for j in live
        if text in [str(r).lower() for r in (j.role_ids or [])]
        or (token.isdigit() and j.objective_id and j.objective is not None and j.objective.seq == int(token))
    ]
    hint = (
        " Matching agents: " + "; ".join(f"{j.pk} ({agent_label(j)})" for j in matches[:6])
        if matches
        else ""
    )
    return None, (
        f"{spec!r} is not an agent id — messages go to one agent, addressed by its id "
        f"(see list_team).{hint}"
    )


def team_listing(project: Project, *, viewer: Job | None = None) -> str:
    """One line per agent: who to message and what state it is in."""
    alias = _alias(project.pk)
    jobs = (
        Job.objects.using(alias)
        .filter(project_id=project.pk)
        .exclude(status__in=TERMINAL_JOB_STATUSES)
        .select_related("objective")
        .order_by("objective__seq", "created_at")[:80]
    )
    lines = []
    for j in jobs:
        me = " (you)" if viewer is not None and j.pk == viewer.pk else ""
        seq = f"OBJ-{j.objective.seq}" if j.objective_id and j.objective else "-"
        parent = f" parent={short_id(j.parent_id)}" if j.parent_id else ""
        wait = f" — waiting: {j.idle_note}" if j.status == JobStatus.IDLE and j.idle_note else ""
        lines.append(
            f"id={j.pk} {seq} role={','.join(j.role_ids or []) or '-'}{parent} "
            f"state={a2a_state(j.status)} [{j.status}]{me}{wait}"
        )
    header = "Address a message to ONE agent by its id (full id, or its first 8 characters):"
    return header + "\n" + "\n".join(lines) if lines else "No live agents on this project."
