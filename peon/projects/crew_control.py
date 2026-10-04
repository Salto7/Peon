"""CrewAI-aware operator controls (pause / resume / replan / reprompt).

Builds on ProjectLifecycle; keeps crew_status / crew_flow_id in sync.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

from peon.projects.lifecycle import ProjectLifecycle
from peon.projects.models import Job, JobLifecycle, JobStatus, Project, ProjectStatus
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


def pause_project(project: Project) -> Project:
    project = ProjectLifecycle.pause_project(project)
    set_crew_status(project, "paused")
    return project


def resume_project(project: Project, *, steer: str = "") -> Project:
    project = ProjectLifecycle.resume_project(project, resume_jobs=True)
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
    if project.status == ProjectStatus.CANCELLED:
        raise RuntimeError("Project is cancelled — cannot replan")
    if project.status == ProjectStatus.PAUSED:
        project.status = ProjectStatus.ACTIVE
        project.save(update_fields=["status", "updated_at"])

    if agent_module() != "crewai":
        return ProjectLifecycle.replan_from_prompt(project, text)

    job = _ensure_manager_job(project, resume=True, replan=True, steer=text)
    set_crew_status(project, "running")
    return {
        "mode": "crew_replan",
        "job_ids": [str(job.id)] if job else [],
        "primary_job_id": str(job.id) if job else "",
        "objectives": 0,
        "plan_preview": text[:240],
    }


def reprompt_manager(
    project: Project, message: str, *, kind: str = "steer"
) -> dict[str, Any]:
    """Inject operator text into the manager job and (re)queue it."""
    text = (message or "").strip()
    if not text:
        raise ValueError("Reprompt message is required")
    ProjectLifecycle.project_allows_operator(project)
    job = _ensure_manager_job(project, resume=True, steer=text)
    if job is not None:
        ProjectLifecycle.enqueue_job_directive(job, text, kind=kind)
    set_crew_status(project, "running")
    return {
        "mode": "crew_reprompt",
        "job_ids": [str(job.id)] if job else [],
        "primary_job_id": str(job.id) if job else "",
    }


def _ensure_manager_job(
    project: Project,
    *,
    resume: bool = False,
    replan: bool = False,
    steer: str = "",
) -> Job | None:
    """Find or create the engagement-manager job and enqueue it."""
    from orchestrator.crew.roles.hierarchy import manager_role

    mgr = manager_role()
    if mgr is None:
        return None
    job = None
    for candidate in project.jobs.exclude(status=JobStatus.CANCELLED).order_by(
        "-created_at"
    ):
        if mgr.id in (candidate.role_ids or []):
            job = candidate
            break
    if job is None:
        job = Job.objects.create(
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
        if steer:
            job.description = (
                f"{(job.description or '').strip()}\n\nOPERATOR:\n{steer}".strip()
            )[:8000]
        job.status = JobStatus.PENDING
        job.error = ""
        job.completed_at = None
        if resume:
            job.resume_from_checkpoint = True
        job.save(
            update_fields=[
                "description",
                "status",
                "error",
                "completed_at",
                "resume_from_checkpoint",
                "updated_at",
            ]
        )
    # Stash replan flag on description prefix consumed via extras in worker — use directive.
    if replan and steer:
        ProjectLifecycle.enqueue_job_directive(
            job, f"REPLAN:\n{steer}", kind="steer"
        )
    enqueue_job(str(job.id))
    return job
