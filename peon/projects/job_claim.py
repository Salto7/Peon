"""Job claim / slotting for the project worker."""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone as dj_tz

from peon.projects.models import Job, JobStatus, ProjectStatus
from peon.projects.runtime_settings import PeonSettings
from peon.projects.streaming import emit_job_stream


def _emit(job: Job, message_type: str, content: str, **meta) -> None:
    emit_job_stream(job, message_type, content, meta or None, swallow_errors=True)


def mark_job_running(job: Job) -> Job | None:
    """Atomically PENDING → RUNNING. Returns refreshed job or None if lost race."""
    updated = Job.objects.filter(pk=job.pk, status=JobStatus.PENDING).update(
        status=JobStatus.RUNNING,
        started_at=dj_tz.now(),
        error="",
    )
    if not updated:
        return None
    job.refresh_from_db()
    return job


def reclaim_stuck_jobs(*, older_than_seconds: int | None = None) -> int:
    """Mark long-stuck RUNNING jobs as FAILED so the queue can continue.

    Returns the number of jobs reclaimed. Uses RuntimeSettings when available.
    """

    seconds = older_than_seconds
    if seconds is None:
        seconds = PeonSettings.get_int("JOB_STUCK_RUNNING_SECONDS", 7200)
    seconds = max(60, int(seconds))
    cutoff = dj_tz.now() - timedelta(seconds=seconds)
    stuck = list(
        Job.objects.filter(status=JobStatus.RUNNING, updated_at__lt=cutoff)[:50]
    )
    n = 0
    for job in stuck:
        job.status = JobStatus.FAILED
        job.error = (job.error or "")[:1800] + (
            f"\n[reclaimed] stuck RUNNING > {seconds}s"
        )
        job.completed_at = dj_tz.now()
        job.save(update_fields=["status", "error", "completed_at", "updated_at"])
        _emit(job, "error", f"Reclaimed stuck RUNNING job (>{seconds}s)")
        n += 1
    return n


def claim_next_job() -> Job | None:
    """Atomically move one PENDING job to RUNNING.

    Skips paused/cancelled projects, children whose parent is stopped, and jobs
    that would exceed parallel project / per-project agent caps.
    """
    reclaim_stuck_jobs()
    with transaction.atomic():
        qs = Job.objects.filter(status=JobStatus.PENDING).order_by("created_at")
        for job in qs.select_related("project", "parent")[:20]:
            project = job.project
            if project is not None and project.status != ProjectStatus.ACTIVE:
                continue
            parent = job.parent
            if parent is not None and parent.status != JobStatus.RUNNING:
                job.status = JobStatus.CANCELLED
                job.error = f"Orphaned: parent job is {parent.status}"
                job.completed_at = dj_tz.now()
                job.save(
                    update_fields=["status", "error", "completed_at", "updated_at"]
                )
                continue
            if not job_slots_available(job):
                continue
            claimed = mark_job_running(job)
            if claimed is not None:
                return claimed
        return None


def job_slots_available(job: Job) -> bool:
    """True if claiming/running this job would respect parallel caps."""

    max_projects = PeonSettings.get_int("MAX_PARALLEL_PROJECTS", 3)
    max_agents = PeonSettings.get_int("MAX_AGENTS_PER_PROJECT", 2)
    running = Job.objects.filter(status=JobStatus.RUNNING)
    if job.project_id:
        project_ids = set(
            running.exclude(project_id=None).values_list("project_id", flat=True)
        )
        if job.project_id not in project_ids and len(project_ids) >= max_projects:
            return False
        if running.filter(project_id=job.project_id).count() >= max_agents:
            return False
    return True
