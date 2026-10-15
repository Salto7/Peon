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
import uuid
from typing import Any, Iterable

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
        sender = m.sender or "agent"
        blocks.append(f"[{label} from {sender}]\n{m.text}")
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
) -> AgentMessage:
    """Deliver an A2A message to ``to_job`` and wake it if it is idle."""
    body = (text or "").strip()
    if not body:
        raise ValueError("Message text is required")
    if to_job.status in TERMINAL_JOB_STATUSES:
        raise ValueError(
            f"Agent {to_job.title!r} has finished ({to_job.status}); "
            "spawn new work instead of messaging it."
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
        },
    )
    alias = _alias(project.pk)
    row = AgentMessage.objects.using(alias).create(
        project=project,
        objective=to_job.objective,
        from_job=from_job,
        to_job=to_job,
        sender=sender_label[:96],
        kind=kind,
        message=msg,
    )
    to_label = ((to_job.role_ids or [""])[0] or to_job.title or "agent").strip()
    _emit(
        to_job,
        "log",
        f"{sender_label} → {to_label} [{kind}]: {body[:400]}",
        tag="a2a",
        event="a2a_message",
        a2a_kind=str(kind),
        a2a_from=sender_label,
        a2a_to=to_label,
        from_job_id=str(from_job.pk) if from_job else "",
        to_job_id=str(to_job.pk),
    )
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
    """Find the live agent a message is for.

    ``spec`` is a job id, ``OBJ-<seq>`` / ``<seq>``, or a role id. Returns
    ``(job, "")`` or ``(None, reason)`` (reason lists candidates when ambiguous).
    """
    text = (spec or "").strip()
    if not text:
        return None, "recipient is required (job id, OBJ-n or role id)"
    alias = _alias(project.pk)
    live = Job.objects.using(alias).filter(project_id=project.pk).exclude(
        status__in=TERMINAL_JOB_STATUSES
    )
    if sender is not None:
        live = live.exclude(pk=sender.pk)

    try:
        jid = uuid.UUID(text)
    except ValueError:
        jid = None
    if jid is not None:
        job = Job.objects.using(alias).filter(project_id=project.pk, pk=jid).first()
        if job is None:
            return None, f"no agent {text} on this project"
        return job, ""

    token = text.upper().removeprefix("OBJ-").removeprefix("OBJ")
    if token.strip().isdigit():
        seq = int(token)
        cands = list(live.filter(objective__seq=seq, parent__isnull=True))
        if not cands:
            return None, f"no live agent on OBJ-{seq}"
        return cands[0], ""

    role = text.lower()
    cands = [j for j in live if role in [str(r).lower() for r in (j.role_ids or [])]]
    if len(cands) == 1:
        return cands[0], ""
    if not cands:
        return None, f"no live agent with role {text!r}"
    listing = "; ".join(f"{j.pk} (OBJ-{getattr(j.objective, 'seq', '?')})" for j in cands[:6])
    return None, f"role {text!r} matches several agents — address one by id: {listing}"


def team_listing(project: Project, *, viewer: Job | None = None) -> str:
    """One line per agent: who to message and what state it is in."""
    alias = _alias(project.pk)
    jobs = (
        Job.objects.using(alias)
        .filter(project_id=project.pk, parent__isnull=True)
        .select_related("objective")
        .order_by("objective__seq", "created_at")[:60]
    )
    lines = []
    for j in jobs:
        me = " (you)" if viewer is not None and j.pk == viewer.pk else ""
        seq = f"OBJ-{j.objective.seq}" if j.objective_id and j.objective else "-"
        wait = f" — waiting: {j.idle_note}" if j.status == JobStatus.IDLE and j.idle_note else ""
        lines.append(
            f"{j.pk} {seq} role={','.join(j.role_ids or []) or '-'} "
            f"state={a2a_state(j.status)} [{j.status}]{me}{wait}"
        )
    return "\n".join(lines) or "No agents on this project yet."
