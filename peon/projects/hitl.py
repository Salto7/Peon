"""Shared human-in-the-loop helpers (prompts, failure exhaustion, pending lists)."""

from __future__ import annotations

import logging
from typing import Any

from django.urls import reverse
from django.utils import timezone as dj_tz

from peon.projects.models import (
    Job,
    Objective,
    ObjectiveStatus,
    OperatorPrompt,
    OperatorPromptKind,
    Project,
)

logger = logging.getLogger(__name__)

# Structured option label for phase-advance HITL (must match prompt.options).
PHASE_ADVANCE_OPTION = "Advance phase"


def create_operator_prompt(
    project: Project,
    question: str,
    *,
    job: Job | None = None,
    kind: str = OperatorPromptKind.AGENT_ASK,
    options: list[str] | None = None,
) -> OperatorPrompt:
    """Record a HITL ask and emit a NEED feed line for the console."""
    from peon.projects.streaming import anchor_job, emit_job_stream, stream_meta

    text = (question or "").strip()
    if not text:
        raise ValueError("question is required")
    kind_norm = (kind or OperatorPromptKind.AGENT_ASK).strip().lower()
    if kind_norm not in OperatorPromptKind.values:
        kind_norm = OperatorPromptKind.AGENT_ASK
    opts = [str(o).strip() for o in (options or []) if str(o).strip()]
    from peon.projects.db import ensure_project_database, using_project

    alias = ensure_project_database(project.pk)
    with using_project(project.pk):
        prompt = OperatorPrompt.objects.using(alias).create(
            project=project,
            job=job,
            kind=kind_norm,
            question=text,
            options=opts,
        )
    anchor = job or anchor_job(project)
    if anchor is not None:
        emit_job_stream(
            anchor,
            "status",
            f"Operator input needed: {text}",
            stream_meta(
                project,
                role="assistant",
                tag="need",
                event="need_input",
                prompt_id=str(prompt.id),
                prompt_kind=kind_norm,
            ),
        )
    return prompt


def create_operator_prompt_once(
    project: Project,
    question: str,
    *,
    job: Job,
    kind: str,
    options: list[str] | None = None,
) -> OperatorPrompt | None:
    """Create a prompt unless this job already has an open one of the same kind."""
    from peon.projects.db import ensure_project_database

    alias = ensure_project_database(job.project_id)
    has_open = (
        OperatorPrompt.objects.using(alias)
        .filter(job=job, kind=kind, resolved_at__isnull=True)
        .exists()
    )
    if has_open:
        return None
    return create_operator_prompt(project, question, job=job, kind=kind, options=options)


def _prompt_row(
    prompt: OperatorPrompt,
    *,
    include_project: bool = False,
    href: str = "",
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": str(prompt.id),
        "kind": prompt.kind or OperatorPromptKind.AGENT_ASK,
        "question": prompt.question,
        "options": list(prompt.options or []),
        "job_id": str(prompt.job_id) if prompt.job_id else "",
        "job_title": (prompt.job.title if prompt.job_id else "") or "",
        "created_at": prompt.created_at.isoformat() if prompt.created_at else "",
    }
    if include_project:
        row["project_id"] = str(prompt.project_id)
        row["project_title"] = prompt.project.title if prompt.project_id else ""
        row["href"] = href
    return row


def _blocked_objective_row(
    obj: Objective,
    *,
    include_project: bool = False,
    href: str = "",
) -> dict[str, Any]:
    reason = (obj.blocked_reason or "").strip() or "Objective is blocked"
    row: dict[str, Any] = {
        "id": f"obj-{obj.id}",
        "kind": OperatorPromptKind.BLOCKED_OBJECTIVE,
        "question": f"OBJ-{obj.seq} {obj.title}: {reason}",
        "options": [],
        "job_id": "",
        "job_title": "",
        "objective_id": str(obj.id),
        "created_at": obj.updated_at.isoformat() if obj.updated_at else "",
    }
    if include_project:
        row["project_id"] = str(obj.project_id)
        row["project_title"] = obj.project.title if obj.project_id else ""
        row["href"] = href
    return row


def _blocked_prompt_still_open(prompt: OperatorPrompt) -> bool:
    """Drop stale blocked_objective rows once the objective left BLOCKED."""
    if prompt.kind != OperatorPromptKind.BLOCKED_OBJECTIVE:
        return True
    job = prompt.job
    if job is None or not job.objective_id:
        return True
    obj = job.objective
    return bool(obj and obj.status == ObjectiveStatus.BLOCKED)


def pending_operator_prompts(project: Project) -> list[dict[str, Any]]:
    """Open HITL prompts + blocked objectives for one project."""
    from peon.projects.db import ensure_project_database, using_project

    out: list[dict[str, Any]] = []
    covered_objectives: set[str] = set()
    alias = ensure_project_database(project.pk)
    with using_project(project.pk):
        for p in (
            OperatorPrompt.objects.using(alias)
            .filter(project_id=project.pk, resolved_at__isnull=True)
            .select_related("job", "job__objective")
            .order_by("created_at")[:40]
        ):
            if not _blocked_prompt_still_open(p):
                continue
            out.append(_prompt_row(p))
            if (
                p.kind == OperatorPromptKind.BLOCKED_OBJECTIVE
                and p.job_id
                and p.job is not None
                and p.job.objective_id
            ):
                covered_objectives.add(str(p.job.objective_id))
            if len(out) >= 20:
                break
        for obj in (
            Objective.objects.using(alias)
            .filter(project_id=project.pk, status=ObjectiveStatus.BLOCKED)
            .order_by("seq")[:20]
        ):
            if str(obj.id) in covered_objectives:
                continue
            out.append(_blocked_objective_row(obj))
    return out


def pending_inputs_global(
    *, prompt_limit: int = 40, blocked_limit: int = 20
) -> list[dict[str, Any]]:
    """Cross-project open HITL prompts + blocked objectives for the global popup."""
    from peon.projects.db import iter_project_aliases

    items: list[dict[str, Any]] = []
    covered_objectives: set[str] = set()
    for pid, alias in iter_project_aliases():
        project = Project.objects.filter(pk=pid).first()
        if project is None:
            continue
        for prompt in (
            OperatorPrompt.objects.using(alias)
            .filter(resolved_at__isnull=True)
            .select_related("job", "job__objective")
            .order_by("created_at")[: max(1, prompt_limit)]
        ):
            if not _blocked_prompt_still_open(prompt):
                continue
            # Attach platform registry project for display helpers
            prompt.project = project
            href = reverse("project_detail", kwargs={"pk": pid}) + "#war-chat"
            items.append(_prompt_row(prompt, include_project=True, href=href))
            if (
                prompt.kind == OperatorPromptKind.BLOCKED_OBJECTIVE
                and prompt.job is not None
                and prompt.job.objective_id
            ):
                covered_objectives.add(str(prompt.job.objective_id))
            if len(items) >= prompt_limit:
                break
        if len(items) >= prompt_limit:
            break
    if len(items) < prompt_limit + blocked_limit:
        blocked_n = 0
        for pid, alias in iter_project_aliases():
            for obj in (
                Objective.objects.using(alias)
                .filter(status=ObjectiveStatus.BLOCKED)
                .order_by("updated_at")[:blocked_limit]
            ):
                if str(obj.id) in covered_objectives:
                    continue
                href = reverse("project_detail", kwargs={"pk": pid}) + "#results"
                items.append(
                    _blocked_objective_row(obj, include_project=True, href=href)
                )
                blocked_n += 1
                if blocked_n >= blocked_limit:
                    break
            if blocked_n >= blocked_limit:
                break
    items.sort(key=lambda r: r.get("created_at") or "")
    return items


def failure_exhausted_prompt(job: Job, error: str, *, fails: int, max_r: int) -> OperatorPrompt | None:
    """Create HITL when failure replans are exhausted (idempotent per open prompt)."""
    if not job.project_id:
        return None
    obj = job.objective
    seq = getattr(obj, "seq", "?")
    roles = [str(r).strip() for r in (job.role_ids or []) if str(r).strip()]
    question = (
        f"Failure replans exhausted ({fails}/{max_r}) for OBJ-{seq} "
        f"({', '.join(roles) or 'unknown role'}): {(error or 'failed')[:500]}\n"
        "Approve to continue with guidance, Edit to steer, or Deny to leave blocked."
    )
    from peon.projects.db import project_alias_or_default

    alias = project_alias_or_default(job.project_id)
    existing = (
        OperatorPrompt.objects.using(alias)
        .filter(
            project_id=job.project_id,
            job=job,
            kind=OperatorPromptKind.FAILURE_EXHAUSTED,
            resolved_at__isnull=True,
        )
        .first()
    )
    if existing:
        return existing
    return create_operator_prompt(
        job.project,
        question,
        job=job,
        kind=OperatorPromptKind.FAILURE_EXHAUSTED,
        options=["approve", "edit", "deny"],
    )


def _advance_objective_phase(job: Job | None) -> None:
    """Advance ``job.objective`` one kill-chain phase (no-op at the end)."""
    if job is None or job.objective_id is None or job.objective is None:
        return
    from peon.projects.platform_catalog import KILL_CHAIN_PHASE_ORDER

    obj = job.objective
    try:
        idx = KILL_CHAIN_PHASE_ORDER.index(obj.phase)
    except ValueError:
        return
    if idx + 1 >= len(KILL_CHAIN_PHASE_ORDER):
        return
    obj.phase = KILL_CHAIN_PHASE_ORDER[idx + 1]
    obj.save(update_fields=["phase", "updated_at"])


def resolve_operator_prompt(
    prompt: OperatorPrompt,
    reply: str,
    *,
    decision: str = "edit",
) -> dict[str, Any]:
    """Apply operator approve/edit/deny and steer the waiting job(s)."""
    from peon.projects.lifecycle import enqueue_job_directive, queue_job
    from peon.projects.models import TERMINAL_JOB_STATUSES, JobDirectiveKind
    from peon.projects.streaming import (
        anchor_job,
        emit_job_stream,
        stream_meta,
        with_stream_reply,
    )

    decision_norm = (decision or "edit").strip().lower()
    raw = (reply or "").strip()
    if decision_norm == "deny":
        text = raw or "DENIED by operator"
    elif decision_norm == "approve":
        text = raw or "APPROVED by operator"
    else:
        text = raw
        if not text:
            raise ValueError("Reply is required")
    prompt.reply = text
    prompt.resolved_at = dj_tz.now()
    prompt.save(update_fields=["reply", "resolved_at"])

    if (
        prompt.kind == OperatorPromptKind.PHASE_ADVANCE_REQUEST
        and text == PHASE_ADVANCE_OPTION
    ):
        _advance_objective_phase(prompt.job)

    # Typed answers are durable knowledge. Bare approve/deny stubs are not.
    store_feedback = decision_norm == "edit" or (
        decision_norm == "approve" and bool(raw)
    )
    if store_feedback:
        try:
            from peon.projects import memory_store

            memory_store.record_feedback(
                prompt.project,
                f"Question from an agent: {prompt.question}\nOperator answer: {text}",
                source="reply to an agent question",
            )
        except Exception:  # noqa: BLE001
            logger.debug("hitl feedback capture failed", exc_info=True)

    note = (
        "OPERATOR REPLY (human-in-the-loop):\n"
        f"Kind: {prompt.kind}\n"
        f"Question: {prompt.question}\n"
        f"Answer: {text}\n\n"
        "Honor this under Rules of Engagement and continue the objective."
    )
    job = prompt.job
    if job is not None and job.objective_id and job.objective is not None:
        try:
            from peon.projects.objectives import ObjectiveScheduler

            ObjectiveScheduler().resume_blocked(
                job.objective, reason="Operator replied"
            )
        except Exception as exc:
            logger.warning("resuming blocked objective failed: %s", exc, exc_info=True)
    steered: list[str] = []
    if job is not None and job.status not in TERMINAL_JOB_STATUSES:
        enqueue_job_directive(
            job, note, kind=JobDirectiveKind.STEER
        )
        steered.append(str(job.id))
    elif job is not None and job.status in TERMINAL_JOB_STATUSES:
        enqueue_job_directive(
            job, note, kind=JobDirectiveKind.FOLLOWUP
        )
        queue_job(job)
        steered.append(str(job.id))
    else:
        job = anchor_job(prompt.project)
        if job is not None and job.status not in TERMINAL_JOB_STATUSES:
            enqueue_job_directive(
                job, note, kind=JobDirectiveKind.STEER
            )
            steered.append(str(job.id))

    if job is not None:
        emit_job_stream(
            job,
            "log",
            text,
            stream_meta(
                prompt.project,
                role="user",
                tag="you",
                event="operator_reply",
                prompt_id=str(prompt.id),
                prompt_kind=prompt.kind,
            ),
        )
        # Durable learning: structured transcript + peon-wide memory.
        try:
            from orchestrator.agent.transcript import record_from_event
            from peon.projects.agent_bridge import JobAgentBridge

            roles = [
                str(r).strip()
                for r in (job.role_ids or [])
                if str(r).strip()
            ]
            rec = record_from_event(
                category="method",
                summary=(
                    f"Operator guidance ({prompt.kind}): "
                    f"{(text or '')[:400]}"
                ),
                project_id=str(prompt.project_id or ""),
                objective_id=str(job.objective_id or ""),
                job_id=str(job.id),
                role_id=roles[0] if roles else "",
                event="operator_guidance",
                payload={
                    "prompt_id": str(prompt.id),
                    "prompt_kind": prompt.kind,
                    "question": (prompt.question or "")[:1000],
                    "reply": text[:2000],
                },
            )
            JobAgentBridge(job).append_transcript(rec)
        except Exception as exc:
            logger.warning("operator reply transcript failed: %s", exc, exc_info=True)
        try:
            from orchestrator.crew.memory import MemoryService

            MemoryService.remember_peon(
                (
                    f"Operator guidance on {prompt.kind}: "
                    f"Q={(prompt.question or '')[:240]} "
                    f"A={text[:400]}"
                ),
                category="method",
                event="operator_guidance",
                project_id=str(prompt.project_id or ""),
            )
        except Exception as exc:
            logger.warning("MemoryService.remember_peon failed: %s", exc, exc_info=True)
    reply_msg = (
        f"Reply delivered to {len(steered)} job(s). "
        "They will continue under Rules of Engagement."
    )
    return with_stream_reply(
        {
            "ok": True,
            "mode": "reply",
            "prompt_id": str(prompt.id),
            "kind": prompt.kind,
            "job_ids": steered,
            "primary_job_id": str(job.id) if job else "",
        },
        prompt.project,
        reply_msg,
        job=job,
        tag="steer",
        event="operator_reply_acked",
    )


def first_open_prompt(project: Project) -> OperatorPrompt | None:
    from peon.projects.db import qs_using

    return (
        qs_using(OperatorPrompt, project.id)
        .filter(project_id=project.id, resolved_at__isnull=True)
        .order_by("created_at")
        .first()
    )
