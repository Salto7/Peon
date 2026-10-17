"""Job claim / slotting for the project worker."""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone as dj_tz

from peon.projects.db import (
    ensure_project_database,
    iter_project_aliases,
    project_alias_or_default,
    refresh_project_kpis,
    using_project,
)
from peon.projects.models import Job, JobStatus, Project, ProjectStatus
from peon.projects.runtime_settings import PeonSettings
from peon.projects.streaming import emit_job_stream

logger = logging.getLogger(__name__)


def emit(job: Job, message_type: str, content: str, **meta) -> None:
    """Stream a job event (swallow errors so workers stay alive)."""
    emit_job_stream(job, message_type, content, meta or None, swallow_errors=True)


def mark_job_running(job: Job) -> Job | None:
    """Atomically PENDING → RUNNING. Returns refreshed job or None if lost race."""
    alias = project_alias_or_default(job.project_id)
    with using_project(job.project_id):
        updated = Job.objects.using(alias).filter(
            pk=job.pk, status=JobStatus.PENDING
        ).update(
            status=JobStatus.RUNNING,
            started_at=dj_tz.now(),
            error="",
        )
        if not updated:
            return None
        job = Job.objects.using(alias).get(pk=job.pk)
    if job.project_id:
        # Best-effort dashboard counters: the PENDING -> RUNNING transition just
        # above already committed. A failure here (e.g. sqlite "database is
        # locked" under concurrent writers) must never bubble up as if the claim
        # itself failed — dramatiq would retry the message, and the retry's
        # "already running" guard would then leave this job stuck forever with
        # no actual work ever started. See finish_job's identical guard.
        try:
            refresh_project_kpis(job.project_id)
        except Exception as exc:
            logger.warning("refresh_project_kpis failed: %s", exc, exc_info=True)
    return job


def reclaim_stuck_jobs(*, older_than_seconds: int | None = None) -> int:
    """Mark long-stuck RUNNING jobs as FAILED so the queue can continue."""

    seconds = older_than_seconds
    if seconds is None:
        seconds = PeonSettings.get_int("JOB_STUCK_RUNNING_SECONDS", 7200)
    seconds = max(60, int(seconds))
    cutoff = dj_tz.now() - timedelta(seconds=seconds)
    from peon.projects.job_run import finish_job

    def _fail_stuck(jobs: list[Job]) -> int:
        count = 0
        for job in jobs:
            err = (job.error or "").strip()
            note = f"[reclaimed] stuck RUNNING > {seconds}s"
            finish_job(
                job,
                status=JobStatus.FAILED,
                error=(f"{err}\n{note}" if err else note)[:2000],
            )
            count += 1
        return count

    n = 0
    # Platform orphan jobs
    n += _fail_stuck(
        list(
            Job.objects.using("default").filter(
                status=JobStatus.RUNNING, updated_at__lt=cutoff, project_id__isnull=True
            )[:50]
        )
    )

    for project_id, alias in iter_project_aliases():
        stuck = list(
            Job.objects.using(alias).filter(
                status=JobStatus.RUNNING, updated_at__lt=cutoff
            )[:50]
        )
        with using_project(project_id):
            n += _fail_stuck(stuck)
    return n


def claim_next_job() -> Job | None:
    """Atomically move one PENDING job to RUNNING across project DBs."""
    reclaim_stuck_jobs()
    orphan_parent = {
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        JobStatus.PAUSED,
    }

    # Fair-ish: active projects first by created_at, then platform orphans.
    active_ids = list(
        Project.objects.filter(status=ProjectStatus.ACTIVE)
        .order_by("created_at")
        .values_list("id", flat=True)
    )
    for pid in active_ids:
        claimed = _claim_in_alias(str(pid), ensure_project_database(pid), orphan_parent)
        if claimed is not None:
            return claimed

    return _claim_in_alias(None, "default", orphan_parent, orphans_only=True)


def _claim_in_alias(
    project_id: str | None,
    alias: str,
    orphan_parent: set[str],
    *,
    orphans_only: bool = False,
) -> Job | None:
    """Claim one PENDING job without holding a long project-DB transaction.

    Slot checks touch every project alias; doing that inside ``atomic`` kept a
    read/write transaction open and blocked finish_job → enqueue_ready.
    """
    with using_project(project_id):
        qs = Job.objects.using(alias).filter(status=JobStatus.PENDING)
        if orphans_only:
            qs = qs.filter(project_id__isnull=True)
        candidates = list(
            qs.select_related("parent", "objective").order_by("created_at")[:20]
        )

    for job in candidates:
        if project_id:
            project = Project.objects.filter(pk=project_id).first()
            if project is None or project.status != ProjectStatus.ACTIVE:
                continue
        if job.objective_id and job.objective is not None:
            from peon.projects.objectives import ObjectiveScheduler

            if not ObjectiveScheduler().dependencies_met(job.objective):
                continue
        parent = job.parent
        if parent is not None and parent.status in orphan_parent:
            with using_project(project_id), transaction.atomic(using=alias):
                locked = (
                    Job.objects.using(alias)
                    .select_for_update()
                    .filter(pk=job.pk, status=JobStatus.PENDING)
                    .first()
                )
                if locked is None:
                    continue
                locked.status = JobStatus.CANCELLED
                locked.error = f"Orphaned: parent job is {parent.status}"
                locked.completed_at = dj_tz.now()
                locked.save(
                    update_fields=[
                        "status",
                        "error",
                        "completed_at",
                        "updated_at",
                    ]
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
    max_agents = PeonSettings.get_int("MAX_AGENTS_PER_PROJECT", 8)

    running_project_ids: set[str] = set()
    for pid, alias in iter_project_aliases():
        if Job.objects.using(alias).filter(status=JobStatus.RUNNING).exists():
            running_project_ids.add(str(pid))
    if Job.objects.using("default").filter(
        status=JobStatus.RUNNING, project_id__isnull=True
    ).exists():
        # orphan lane does not count as a project slot
        pass

    if job.project_id:
        pid = str(job.project_id)
        if pid not in running_project_ids and len(running_project_ids) >= max_projects:
            return False
        alias = ensure_project_database(pid)
        if (
            Job.objects.using(alias)
            .filter(project_id=pid, status=JobStatus.RUNNING)
            .count()
            >= max_agents
        ):
            return False
    return True
