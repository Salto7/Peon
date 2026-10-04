"""Project-level lifecycle (pause, resume, delete, replan, bulk)."""

from __future__ import annotations

from collections.abc import Iterable

from django.db import transaction

from peon.projects.models import (
    TERMINAL_JOB_STATUSES,
    JobStatus,
    Project,
    ProjectStatus,
)
from peon.projects.tasks import enqueue_sandbox_cleanup
from peon.projects.workspaces import remove_project_workspace
from peon.projects.planning_persist import PlanningService

_PROJECT_PAUSE_NOTE = "Paused with project"
_TERMINAL_JOB = TERMINAL_JOB_STATUSES


class LifecycleProjectsMixin:
    """Project-scoped operator lifecycle."""

    @classmethod
    def pause_project(cls, project: Project) -> Project:
        """Pause project and all non-terminal jobs."""
        project.status = ProjectStatus.PAUSED
        project.save(update_fields=["status", "updated_at"])
        for job in project.jobs.exclude(status__in=_TERMINAL_JOB):
            if job.status != JobStatus.PAUSED:
                cls._apply_directive(job, "pause")
                job.refresh_from_db()
                if job.status == JobStatus.PAUSED:
                    job.error = _PROJECT_PAUSE_NOTE
                    job.save(update_fields=["error", "updated_at"])
        return project

    @classmethod
    def resume_project(cls, project: Project, *, resume_jobs: bool = True) -> Project:
        """Resume a paused/cancelled project; optionally re-queue jobs paused with it."""
        project.status = ProjectStatus.ACTIVE
        project.completed_at = None
        project.save(update_fields=["status", "completed_at", "updated_at"])
        if resume_jobs:
            for job in project.jobs.filter(status=JobStatus.PAUSED):
                if (job.error or "") == _PROJECT_PAUSE_NOTE:
                    cls._apply_directive(job, "resume")
        return project

    @classmethod
    def delete_project(cls, project: Project) -> dict:
        """Cancel jobs, delete workspace + DB row; queue sandbox docker rm on the worker.

        Docker sandbox removal is asynchronous so the HTTP response can redirect
        before ``docker rm`` flaps host networking (ERR_NETWORK_CHANGED in the browser).
        """
        pid = str(project.id)
        title = project.title
        for job in project.jobs.exclude(status__in=_TERMINAL_JOB):
            cls._apply_directive(job, "kill")

        disk = remove_project_workspace(pid)
        with transaction.atomic():
            Project.objects.filter(pk=pid).delete()
        sandbox = enqueue_sandbox_cleanup(pid)
        return {
            "title": title,
            "project_id": pid,
            "sandbox": sandbox,
            "workspace": disk,
        }

    @classmethod
    def replan_from_prompt(cls, project: Project, message: str = "") -> dict:
        """Operator replan (chat or control bar). Allowed while paused; not when cancelled."""

        if project.status == ProjectStatus.CANCELLED:
            raise RuntimeError("Project is cancelled — cannot replan")
        text = (message or "").strip() or (project.summary or project.title or "").strip()
        if not text:
            raise ValueError("Message is required")
        result = PlanningService.replan_project(project, description=text)
        job = result.job
        return {
            "mode": "replan",
            "job_ids": [str(job.id)] if job is not None else [],
            "primary_job_id": str(job.id) if job is not None else "",
            "objectives": len(result.objectives or []),
            "plan_preview": (result.plan_text or "")[:240],
        }

    @classmethod
    def _projects(cls, ids: Iterable[str]) -> list[Project]:
        wanted = [str(x).strip() for x in ids if str(x).strip()]
        if not wanted:
            return []
        by_id = {str(p.id): p for p in Project.objects.filter(pk__in=wanted)}
        return [by_id[i] for i in wanted if i in by_id]

    @classmethod
    def bulk_pause_projects(cls, ids: Iterable[str]) -> int:
        # circular: crew_control → lifecycle
        from peon.projects.crew_control import pause_project as crew_pause

        n = 0
        for project in cls._projects(ids):
            if project.status == ProjectStatus.ACTIVE:
                crew_pause(project)
                n += 1
        return n

    @classmethod
    def bulk_resume_projects(cls, ids: Iterable[str]) -> int:
        # circular: crew_control → lifecycle
        from peon.projects.crew_control import resume_project as crew_resume

        n = 0
        for project in cls._projects(ids):
            if project.status == ProjectStatus.PAUSED:
                crew_resume(project)
                n += 1
        return n

    @classmethod
    def bulk_delete_projects(cls, ids: Iterable[str]) -> int:
        n = 0
        for project in cls._projects(ids):
            cls.delete_project(project)
            n += 1
        return n

