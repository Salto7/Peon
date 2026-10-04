"""Project status reconciliation helpers."""

from __future__ import annotations

from collections.abc import Iterable

from django.utils import timezone as dj_tz

from peon.projects.models import (
    TERMINAL_JOB_STATUSES,
    JobStatus,
    ObjectiveStatus,
    Project,
    ProjectStatus,
)
from peon.projects.objectives import ObjectiveScheduler

_TERMINAL_JOB = TERMINAL_JOB_STATUSES
_OPEN_OBJECTIVE = frozenset(
    {ObjectiveStatus.PENDING, ObjectiveStatus.IN_PROGRESS, ObjectiveStatus.BLOCKED}
)


class LifecycleStatusMixin:
    """Project status gates and reconciliation."""

    @classmethod
    def cancel_open_objectives_for_roles(cls, 
        project: Project | None, role_ids: Iterable[str]
    ) -> int:
        """Mark still-open objectives for the given roles as cancelled."""
        if project is None:
            return 0
        names = {str(s).strip() for s in role_ids if str(s).strip()}
        if not names:
            return 0
        n = 0
        for obj in project.objectives.filter(
            role_id__in=names, status__in=_OPEN_OBJECTIVE
        ):
            obj.status = ObjectiveStatus.CANCELLED
            obj.save(update_fields=["status", "updated_at"])
            n += 1
        return n

    @classmethod
    def reconcile_project_status(cls, project: Project | None) -> Project | None:
        """Roll project status from jobs + objectives. No-op while paused or cancelled."""
        if project is None:
            return None
        project.refresh_from_db()
        if project.status in {ProjectStatus.PAUSED, ProjectStatus.CANCELLED}:
            return project

        statuses = list(project.jobs.values_list("status", flat=True))
        obj_statuses = list(project.objectives.values_list("status", flat=True))
        has_ready = ObjectiveScheduler().next_ready(project) is not None
        jobs_busy = any(s not in _TERMINAL_JOB for s in statuses) if statuses else False
        objs_busy = any(
            s in {ObjectiveStatus.PENDING, ObjectiveStatus.IN_PROGRESS} for s in obj_statuses
        )

        if jobs_busy or has_ready or (
            objs_busy and any(s == ObjectiveStatus.IN_PROGRESS for s in obj_statuses)
        ):
            if project.status != ProjectStatus.ACTIVE:
                project.status = ProjectStatus.ACTIVE
                project.completed_at = None
                project.save(update_fields=["status", "completed_at", "updated_at"])
            return project

        if not statuses and not obj_statuses:
            return project

        if has_ready:
            return project

        if obj_statuses and all(s == ObjectiveStatus.CANCELLED for s in obj_statuses):
            next_status = ProjectStatus.CANCELLED
        elif any(s == ObjectiveStatus.BLOCKED for s in obj_statuses) or any(
            s == JobStatus.FAILED for s in statuses
        ):
            next_status = ProjectStatus.FINISHED_WITH_ERRORS
        elif obj_statuses and all(
            s in {ObjectiveStatus.COMPLETED, ObjectiveStatus.CANCELLED} for s in obj_statuses
        ):
            next_status = ProjectStatus.FINISHED
        elif statuses and all(s == JobStatus.CANCELLED for s in statuses):
            next_status = ProjectStatus.CANCELLED
        elif any(s == JobStatus.FAILED for s in statuses):
            next_status = ProjectStatus.FINISHED_WITH_ERRORS
        else:
            next_status = ProjectStatus.FINISHED

        fields = ["status", "updated_at"]
        project.status = next_status
        if next_status in {
            ProjectStatus.FINISHED,
            ProjectStatus.FINISHED_WITH_ERRORS,
            ProjectStatus.CANCELLED,
        }:
            project.completed_at = dj_tz.now()
            fields.append("completed_at")
        project.save(update_fields=fields)
        if next_status in {
            ProjectStatus.FINISHED,
            ProjectStatus.FINISHED_WITH_ERRORS,
            ProjectStatus.CANCELLED,
        } and (project.crew_status or "").strip():
            # circular: crew_control → lifecycle
            from peon.projects.crew_control import set_crew_status

            set_crew_status(
                project,
                "done" if next_status == ProjectStatus.FINISHED else "stopped",
            )
        return project

    @classmethod
    def project_accepts_work(cls, project: Project | None) -> bool:
        if project is None:
            return True
        return project.status == ProjectStatus.ACTIVE

    @classmethod
    def project_allows_operator(cls, project: Project | None) -> None:
        """Raise if the project cannot accept instruct / re-run (not replan/stop)."""
        if project is None:
            return
        if project.status == ProjectStatus.CANCELLED:
            raise RuntimeError("Project is cancelled — cannot run operator actions")
        if project.status == ProjectStatus.PAUSED:
            raise RuntimeError("Project is paused — resume before running operator actions")

