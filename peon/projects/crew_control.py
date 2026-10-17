"""CrewAI-aware operator controls (stop / pause / restart / resume / replan / reprompt).

Builds on ``lifecycle``; keeps crew_status / crew_flow_id in sync.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

from orchestrator.crew.roles.registry import RoleRegistry
from peon.projects import lifecycle
from peon.projects.lifecycle import (
    enqueue_job_directive,
    project_allows_operator,
    replan_from_prompt,
    route_operator_instruction,
)
from peon.projects.models import (
    Job,
    JobDirectiveKind,
    JobLifecycle,
    JobStatus,
    Project,
    ProjectStatus,
)
from peon.projects.tasks import enqueue_job


def agent_module() -> str:
    return str(getattr(settings, "AGENT_MODULE", "crewai") or "crewai").strip().lower()


def set_crew_status(project: Project, status: str, *, flow_id: str = "") -> None:
    fields = ["crew_status", "updated_at"]
    project.crew_status = (status or "").strip()[:32]
    if flow_id:
        project.crew_flow_id = flow_id[:64]
        fields.append("crew_flow_id")
    project.save(update_fields=fields)


def _engagement_manager_job(project: Project, manager_id: str) -> Job | None:
    """The engagement's manager job, not the resident governance watcher.

    Prefer a row that is still in play. A cancelled row is reused only when
    it carries the manager objective, so dispatch stays on the bookend job
    instead of a new manager with nothing to settle.
    """
    from peon.projects.a2a import GOVERNANCE_PROFILE

    if not manager_id:
        return None
    candidates = [
        job
        for job in project.jobs.exclude(profile=GOVERNANCE_PROFILE).order_by("-created_at")
        if manager_id in (job.role_ids or [])
    ]
    for job in candidates:
        if job.status != JobStatus.CANCELLED:
            return job
    for job in candidates:
        if job.objective_id:
            return job
    return None


def stop_project(project: Project) -> Project:
    """Cancel in-flight jobs and mark the project stopped. The row is kept."""
    from django.utils import timezone as dj_tz

    project.status = ProjectStatus.CANCELLED
    project.completed_at = dj_tz.now()
    project.save(update_fields=["status", "completed_at", "updated_at"])
    lifecycle.cancel_open_jobs(project)
    set_crew_status(project, "stopped")
    return project


def restart_project(project: Project) -> Project:
    """Stop in-flight attempts and dispatch the plan again.

    Objectives stay as they are. Open ones are queued by the manager bookend
    (``enqueue_ready`` treats an IN_PROGRESS objective with no live job as
    ready). Operator kill remains the action that withdraws an objective.
    """
    from peon.projects.a2a import ensure_governance_pm

    mgr = RoleRegistry.shared().manager()
    manager = _engagement_manager_job(project, mgr.id if mgr is not None else "")
    lifecycle.abandon_open_attempts(project, reuse=manager)
    project.status = ProjectStatus.ACTIVE
    project.completed_at = None
    project.save(update_fields=["status", "completed_at", "updated_at"])
    set_crew_status(project, "running")
    _ensure_manager_job(project, resume=False)
    ensure_governance_pm(project)
    return project


def pause_project(project: Project) -> Project:
    project = lifecycle.pause_project(project)
    set_crew_status(project, "paused")
    return project


def resume_project(project: Project, *, steer: str = "") -> Project:
    project = lifecycle.resume_project(project, resume_jobs=True)
    if agent_module() == "crewai":
        set_crew_status(project, "running")
        note = (steer or "").strip()
        if note:
            reprompt_manager(project, note, kind="steer")
        else:
            _ensure_manager_job(project, resume=True)
    return project


def replan_project(project: Project, message: str) -> dict[str, Any]:
    """Crew-aware replan: queue project-manager with replan steer."""
    text = (message or "").strip()
    if not text:
        raise ValueError("Replan message is required")
    from peon.projects.models import FINISHED_PROJECT_STATUSES

    if project.status == ProjectStatus.CANCELLED:
        raise RuntimeError("Project is cancelled — cannot replan")
    if project.status in FINISHED_PROJECT_STATUSES | {ProjectStatus.PAUSED}:
        project.status = ProjectStatus.ACTIVE
        project.save(update_fields=["status", "updated_at"])

    if agent_module() != "crewai":
        return replan_from_prompt(project, text)

    job = _ensure_manager_job(project, resume=True, replan=True, steer=text)
    set_crew_status(project, "running")
    return {
        "mode": "crew_replan",
        "job_ids": [str(job.id)] if job else [],
        "primary_job_id": str(job.id) if job else "",
        "objectives": 0,
        "plan_preview": text[:240],
    }


def reprompt_manager(project: Project, message: str, *, kind: str = "steer") -> dict[str, Any]:
    """Inject operator text into the manager job and (re)queue it."""
    text = (message or "").strip()
    if not text:
        raise ValueError("Reprompt message is required")
    project_allows_operator(project)
    job = _ensure_manager_job(project, resume=True, steer=text)
    enqueue_job_directive(job, text, kind=kind)
    set_crew_status(project, "running")
    return {
        "mode": "crew_reprompt",
        "job_ids": [str(job.id)],
        "primary_job_id": str(job.id),
    }


def instruct_project(
    project: Project, message: str, *, job_id: str | None = None
) -> dict[str, Any]:
    """Steer live agents; keep crew_status in sync."""
    text = (message or "").strip()
    if not text:
        raise ValueError("Instruction message is required")
    result = route_operator_instruction(project, text, job_id=job_id, record_stream=False)
    if agent_module() == "crewai" and project.status == ProjectStatus.ACTIVE:
        set_crew_status(project, "running")
    return result


def _ensure_manager_job(
    project: Project,
    *,
    resume: bool = False,
    replan: bool = False,
    steer: str = "",
) -> Job:
    """Find or create the engagement-manager job and enqueue it."""

    mgr = RoleRegistry.shared().manager()
    if mgr is None:
        raise RuntimeError(
            "No CrewAI manager role is loaded; check platform roles + skills/ on web and worker"
        )
    job = _engagement_manager_job(project, mgr.id)
    if job is None:
        from peon.projects.db import ensure_project_database

        alias = ensure_project_database(project.id)
        job = Job.objects.using(alias).create(
            title=mgr.label or "Project Manager",
            description=(steer or project.summary or project.title or "")[:8000],
            lifecycle=JobLifecycle.LONG,
            status=JobStatus.PENDING,
            role_ids=[mgr.id],
            project=project,
            workspace_id=str(project.id),
            plan_text="",
        )
    else:
        from peon.projects.db import ensure_project_database

        alias = ensure_project_database(project.id)
        if steer:
            job.description = (f"{(job.description or '').strip()}\n\nOPERATOR:\n{steer}".strip())[
                :8000
            ]
        lifecycle.reopen_objective_for_rerun(job)
        job.status = JobStatus.PENDING
        job.error = ""
        job.completed_at = None
        job.resume_from_checkpoint = bool(resume)
        job.save(
            using=alias,
            update_fields=[
                "description",
                "status",
                "error",
                "completed_at",
                "resume_from_checkpoint",
                "updated_at",
            ],
        )
    if replan and steer:
        enqueue_job_directive(job, steer, kind=JobDirectiveKind.REPLAN)
    enqueue_job(str(job.id))
    return job
