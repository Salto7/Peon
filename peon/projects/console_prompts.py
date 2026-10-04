"""Operator HITL prompts for the project console."""

from __future__ import annotations

from django.utils import timezone as dj_tz

from peon.projects.console_intents import _with_reply
from peon.projects.lifecycle import ProjectLifecycle
from peon.projects.models import (
    TERMINAL_JOB_STATUSES,
    Job,
    JobDirectiveKind,
    Objective,
    ObjectiveStatus,
    OperatorPrompt,
    Project,
)
from peon.projects.streaming import anchor_job, emit_job_stream, stream_meta


def pending_operator_prompts(project: Project) -> list[dict]:
    """Open HITL prompts + blocked objectives that need the operator."""
    out: list[dict] = []
    for p in OperatorPrompt.objects.filter(
        project=project, resolved_at__isnull=True
    ).select_related("job").order_by("created_at")[:20]:
        out.append(
            {
                "id": str(p.id),
                "kind": "prompt",
                "question": p.question,
                "job_id": str(p.job_id) if p.job_id else "",
                "job_title": (p.job.title if p.job_id else "") or "",
                "created_at": p.created_at.isoformat() if p.created_at else "",
            }
        )
    for obj in Objective.objects.filter(
        project=project, status=ObjectiveStatus.BLOCKED
    ).order_by("seq")[:20]:
        reason = (obj.blocked_reason or "").strip() or "Objective is blocked"
        out.append(
            {
                "id": f"obj-{obj.id}",
                "kind": "blocked_objective",
                "question": f"OBJ-{obj.seq} {obj.title}: {reason}",
                "job_id": "",
                "job_title": "",
                "objective_id": str(obj.id),
                "created_at": obj.updated_at.isoformat() if obj.updated_at else "",
            }
        )
    return out


def create_operator_prompt(
    project: Project,
    question: str,
    *,
    job: Job | None = None,
) -> OperatorPrompt:
    """Record a HITL ask and emit a NEED feed line for the console."""
    text = (question or "").strip()
    if not text:
        raise ValueError("question is required")
    prompt = OperatorPrompt.objects.create(
        project=project,
        job=job,
        question=text,
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
            ),
        )
    return prompt


def _resolve_prompt(prompt: OperatorPrompt, reply: str) -> dict:

    text = (reply or "").strip()
    if not text:
        raise ValueError("Reply is required")
    prompt.reply = text
    prompt.resolved_at = dj_tz.now()
    prompt.save(update_fields=["reply", "resolved_at"])

    note = (
        "OPERATOR REPLY (human-in-the-loop):\n"
        f"Question: {prompt.question}\n"
        f"Answer: {text}\n\n"
        "Honor this under Rules of Engagement and continue the objective."
    )
    job = prompt.job
    steered: list[str] = []
    if job is not None and job.status not in TERMINAL_JOB_STATUSES:
        ProjectLifecycle.enqueue_job_directive(
            job, note, kind=JobDirectiveKind.STEER
        )
        steered.append(str(job.id))
    elif job is not None and job.status in TERMINAL_JOB_STATUSES:
        ProjectLifecycle.enqueue_job_directive(
            job, note, kind=JobDirectiveKind.FOLLOWUP
        )
        ProjectLifecycle.queue_job(job)
        steered.append(str(job.id))
    else:
        result = ProjectLifecycle.route_operator_instruction(
            prompt.project, note, record_stream=False
        )
        steered = list(result.get("job_ids") or [])
        job = anchor_job(prompt.project)

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
            ),
        )
    reply = f"Reply delivered to {len(steered)} job(s). They will continue under Rules of Engagement."
    return _with_reply(
        {
            "ok": True,
            "mode": "reply",
            "prompt_id": str(prompt.id),
            "job_ids": steered,
            "primary_job_id": str(job.id) if job else "",
        },
        prompt.project,
        reply,
        job=job,
        tag="steer",
        event="operator_reply_acked",
    )

def _first_pending_prompt(project: Project) -> OperatorPrompt | None:
    return (
        OperatorPrompt.objects.filter(project=project, resolved_at__isnull=True)
        .order_by("created_at")
        .first()
    )
