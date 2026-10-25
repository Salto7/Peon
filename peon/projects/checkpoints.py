"""Mandatory job checkpoints (domain + transcript digest)."""

from __future__ import annotations

import logging
from typing import Any

from peon.projects.db import project_alias_or_default
from peon.projects.models import Job, JobCheckpoint, JobStatus

logger = logging.getLogger(__name__)


def _ckpt_qs(job: Job):
    alias = project_alias_or_default(job.project_id)
    return JobCheckpoint.objects.using(alias).filter(job_id=job.pk)


def _next_seq(job: Job) -> int:
    from django.db.models import Max

    last = _ckpt_qs(job).aggregate(m=Max("seq")).get("m")
    return int(last or 0) + 1


def write_job_checkpoint(
    job: Job,
    *,
    reason: str = "job_finished",
    extra: dict[str, Any] | None = None,
) -> JobCheckpoint:
    """Persist a structured checkpoint for resume / durable continuity."""
    digest = ""
    try:
        from orchestrator.agent.transcript import digest_markdown
        from peon.projects.workspaces import project_workspace_dir

        if job.project_id:
            ws = project_workspace_dir(str(job.project_id), create=False)
            path = ws / "transcript.jsonl"
            if path.is_file():
                digest = digest_markdown(path, limit=40)
    except Exception:
        digest = ""

    context: dict[str, Any] = {
        "job_id": str(job.id),
        "job_status": job.status,
        "job_title": job.title,
        "role_ids": list(job.role_ids or []),
        "error": (job.error or "")[:2000],
        "result_preview": (job.result or "")[:2000],
        "parent_id": str(job.parent_id) if job.parent_id else "",
        "objective_id": str(job.objective_id) if job.objective_id else "",
        "objective_seq": getattr(job.objective, "seq", None)
        if getattr(job, "objective_id", None)
        else None,
    }
    if extra:
        context.update(extra)

    alias = project_alias_or_default(job.project_id)
    ckpt = JobCheckpoint.objects.using(alias).create(
        job=job,
        project_id=job.project_id,
        objective_id=job.objective_id,
        seq=_next_seq(job),
        reason=(reason or "job_finished")[:64],
        context=context,
        transcript_digest=(digest or "")[:16000],
    )
    try:
        from orchestrator.agent.transcript import TranscriptRecord
        from orchestrator.crew.memory import MemoryService

        detail = digest or str(context.get("result_preview") or "")
        summary = "\n".join(
            bit
            for bit in (
                f"checkpoint seq={ckpt.seq} reason={ckpt.reason}",
                f"job={job.title} status={job.status}",
                detail[:2000],
            )
            if bit
        )
        MemoryService.remember_transcript(
            job,
            TranscriptRecord(
                category="checkpoint",
                summary=summary,
                event=reason or "job_finished",
                role_id=(list(job.role_ids or []) or [""])[0],
                job_id=str(job.id),
            ),
        )
    except Exception:
        logger.warning("checkpoint memory write failed for job %s", job.id, exc_info=True)
    return ckpt


def latest_checkpoint(job: Job) -> JobCheckpoint | None:
    return _ckpt_qs(job).order_by("-seq", "-created_at").first()


def resume_context_text(job: Job) -> str:
    """Build continuation instructions from the latest checkpoint."""
    ckpt = latest_checkpoint(job)
    if ckpt is None:
        return ""
    ctx = ckpt.context if isinstance(ckpt.context, dict) else {}
    lines = [
        "## Resume from checkpoint",
        f"checkpoint_seq={ckpt.seq} reason={ckpt.reason}",
        f"prior_status={ctx.get('job_status') or job.status}",
    ]
    if ctx.get("error"):
        lines.append(f"prior_error={ctx['error']}")
    if ctx.get("result_preview"):
        lines.append(f"prior_result={ctx['result_preview']}")
    if ckpt.transcript_digest:
        lines.extend(["", "### Transcript digest", ckpt.transcript_digest[:4000]])
    return "\n".join(lines)


def require_checkpoint_before_terminal(job: Job) -> None:
    """Ensure terminal jobs always have at least one checkpoint row."""
    if job.status not in {
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    }:
        return
    if _ckpt_qs(job).exists():
        return
    write_job_checkpoint(job, reason="job_finished")
