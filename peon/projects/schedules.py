"""Durable ProjectSchedule: fire due cron/interval rows → Dramatiq Jobs."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from django.utils import timezone as dj_tz

from orchestrator.utils.schedule_time import aware_utc, compute_next_run
from peon.projects.checkpoints import write_job_checkpoint
from peon.projects.models import (
    INACTIVE_PROJECT_STATUSES,
    TERMINAL_JOB_STATUSES,
    Job,
    JobStatus,
    Objective,
    ObjectiveStatus,
    Project,
    ProjectKind,
    ProjectSchedule,
    ScheduleKind,
)
from peon.projects.objectives import ObjectiveScheduler
from peon.projects.tasks import enqueue_job

logger = logging.getLogger(__name__)


def arm_schedule(schedule: ProjectSchedule, *, after=None) -> ProjectSchedule:
    """Set ``next_run_at`` from kind/cron/interval."""
    now = aware_utc(after or dj_tz.now())
    if schedule.kind == ScheduleKind.ONCE and schedule.last_run_at:
        schedule.next_run_at = None
        schedule.enabled = False
        schedule.save(update_fields=["next_run_at", "enabled", "updated_at"])
        return schedule
    try:
        nxt = compute_next_run(
            kind=schedule.kind,
            cron=schedule.cron or "",
            interval_seconds=int(schedule.interval_seconds or 0),
            after=now,
            anchor=schedule.last_run_at or schedule.created_at or now,
        )
    except ValueError as exc:
        logger.warning("schedule %s arm failed: %s", schedule.id, exc)
        schedule.enabled = False
        schedule.next_run_at = None
        schedule.save(update_fields=["enabled", "next_run_at", "updated_at"])
        return schedule
    schedule.next_run_at = nxt
    schedule.save(update_fields=["next_run_at", "updated_at"])
    return schedule


def create_schedule(
    project: Project,
    *,
    title: str = "",
    role_id: str = "",
    brief: str = "",
    kind: str = ScheduleKind.INTERVAL,
    cron: str = "",
    interval_seconds: int = 0,
    objective: Objective | None = None,
    enabled: bool = True,
    skip_if_running: bool = True,
) -> ProjectSchedule:
    """Create and arm a schedule (durable projects only recommended)."""
    from peon.projects.db import ensure_project_database, using_project

    alias = ensure_project_database(project.id)
    with using_project(project.id):
        row = ProjectSchedule.objects.using(alias).create(
            project=project,
            objective=objective,
            title=(title or "").strip()[:255],
            role_id=(role_id or "").strip()[:128],
            brief=(brief or "").strip(),
            kind=(
                ScheduleKind(kind)
                if kind in ScheduleKind.values
                else ScheduleKind.INTERVAL
            ),
            cron=(cron or "").strip()[:64],
            interval_seconds=max(0, int(interval_seconds or 0)),
            enabled=bool(enabled),
            skip_if_running=bool(skip_if_running),
        )
    return arm_schedule(row)


def _has_running(schedule: ProjectSchedule) -> bool:
    from peon.projects.db import project_alias_or_default

    alias = project_alias_or_default(schedule.project_id)
    if schedule.last_job_id:
        st = (
            Job.objects.using(alias)
            .filter(pk=schedule.last_job_id)
            .values_list("status", flat=True)
            .first()
        )
        if st and st not in TERMINAL_JOB_STATUSES:
            return True
    if schedule.objective_id:
        return (
            Job.objects.using(alias)
            .filter(objective_id=schedule.objective_id)
            .exclude(status__in=TERMINAL_JOB_STATUSES)
            .exists()
        )
    return False


def fire_schedule(schedule: ProjectSchedule) -> Job | None:
    """Enqueue one Job for a due schedule; advance next_run_at."""
    from peon.projects.db import ensure_project_database, using_project
    from peon.projects.models import Project

    alias = ensure_project_database(schedule.project_id)
    project = Project.objects.get(pk=schedule.project_id)
    if project.status in INACTIVE_PROJECT_STATUSES:
        return None
    if project.kind == ProjectKind.DURABLE and not project.plan_accepted:
        return None

    with using_project(project.id):
        schedule = (
            ProjectSchedule.objects.using(alias)
            .select_related("objective")
            .get(pk=schedule.pk)
        )
        if not schedule.enabled:
            return None
        if schedule.skip_if_running and _has_running(schedule):
            arm_schedule(schedule)
            return None

        job: Job | None = None
        obj = schedule.objective
        if obj is not None:
            if obj.status in {
                ObjectiveStatus.COMPLETED,
                ObjectiveStatus.BLOCKED,
                ObjectiveStatus.CANCELLED,
            }:
                obj.status = ObjectiveStatus.PENDING
                obj.blocked_reason = ""
                obj.completed_at = None
                obj.save(
                    using=alias,
                    update_fields=[
                        "status",
                        "blocked_reason",
                        "completed_at",
                        "updated_at",
                    ],
                )
            job = ObjectiveScheduler().create_run(
                project,
                obj,
                plan_text=schedule.brief or obj.description or "",
                workspace_id=str(project.id),
            )
        else:
            role = (schedule.role_id or "").strip()
            if not role:
                logger.warning("schedule %s has no role_id/objective", schedule.id)
                arm_schedule(schedule)
                return None
            title = (schedule.title or f"Scheduled {role}").strip()[:255]
            job = Job.objects.using(alias).create(
                project=project,
                title=title,
                description=(schedule.brief or title).strip(),
                status=JobStatus.PENDING,
                role_ids=[role],
                workspace_id=str(project.id),
            )
            enqueue_job(str(job.id))

        now = dj_tz.now()
        schedule.last_run_at = now
        schedule.last_job = job
        schedule.save(
            using=alias, update_fields=["last_run_at", "last_job", "updated_at"]
        )
        if job is not None:
            try:
                write_job_checkpoint(
                    job,
                    reason="scheduled_run",
                    extra={
                        "schedule_id": str(schedule.id),
                        "schedule_kind": schedule.kind,
                    },
                )
            except Exception as exc:
                logger.warning("write_job_checkpoint failed: %s", exc, exc_info=True)
        arm_schedule(schedule, after=now)
        return job


def fire_due_schedules(*, now: datetime | None = None, limit: int = 50) -> dict[str, Any]:
    """Process due enabled schedules across active durable (and timed) projects."""
    from peon.projects.db import iter_project_aliases, using_project

    clock = aware_utc(now or dj_tz.now())
    due: list[ProjectSchedule] = []
    for pid, alias in iter_project_aliases():
        with using_project(pid):
            due.extend(
                list(
                    ProjectSchedule.objects.using(alias)
                    .filter(enabled=True, next_run_at__lte=clock)
                    .order_by("next_run_at")[:limit]
                )
            )
    due.sort(key=lambda s: s.next_run_at or clock)
    due = due[:limit]
    fired: list[str] = []
    skipped = 0
    for row in due:
        try:
            job = fire_schedule(row)
            if job is not None:
                fired.append(str(job.id))
            else:
                skipped += 1
        except Exception:
            logger.exception("fire_schedule failed for %s", row.id)
            skipped += 1
    return {"ok": True, "due": len(due), "fired": fired, "skipped": skipped}


def rearm_completed_for_schedules(project: Project) -> int:
    """When durable cycle ends, re-arm objectives that have schedules."""
    if project.kind != ProjectKind.DURABLE:
        return 0
    n = 0
    for sched in project.schedules.filter(enabled=True, objective__isnull=False):
        obj = sched.objective
        if obj is None:
            continue
        if obj.status == ObjectiveStatus.COMPLETED:
            obj.status = ObjectiveStatus.PENDING
            obj.completed_at = None
            obj.save(update_fields=["status", "completed_at", "updated_at"])
            n += 1
        if sched.next_run_at is None:
            arm_schedule(sched)
    return n


def schedules_payload(project: Project) -> list[dict[str, Any]]:
    rows = []
    for s in project.schedules.order_by("next_run_at", "created_at"):
        rows.append(
            {
                "id": str(s.id),
                "title": s.title,
                "role_id": s.role_id,
                "brief": s.brief,
                "kind": s.kind,
                "cron": s.cron,
                "interval_seconds": s.interval_seconds,
                "enabled": s.enabled,
                "skip_if_running": s.skip_if_running,
                "objective_id": str(s.objective_id) if s.objective_id else "",
                "next_run_at": s.next_run_at.isoformat() if s.next_run_at else "",
                "last_run_at": s.last_run_at.isoformat() if s.last_run_at else "",
                "last_job_id": str(s.last_job_id) if s.last_job_id else "",
            }
        )
    return rows
