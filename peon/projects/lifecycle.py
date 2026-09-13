"""Project / Job operator lifecycle: pause, resume, delete (single + bulk)."""

from __future__ import annotations

from collections.abc import Iterable

from django.db import transaction
from django.utils import timezone as dj_tz

from peon.projects.models import (
    TERMINAL_JOB_STATUSES,
    Job,
    JobStatus,
    ObjectiveStatus,
    Project,
    ProjectStatus,
)
from peon.projects.tasks import enqueue_job, enqueue_sandbox_cleanup
from peon.projects.workspaces import remove_project_workspace

_PROJECT_PAUSE_NOTE = "Paused with project"
_TERMINAL_JOB = TERMINAL_JOB_STATUSES
_OPEN_OBJECTIVE = frozenset(
    {ObjectiveStatus.PENDING, ObjectiveStatus.IN_PROGRESS, ObjectiveStatus.BLOCKED}
)




class ProjectLifecycle:
    """Project / Job operator lifecycle: pause, resume, delete (single + bulk)."""

    @classmethod
    def _apply_directive(cls, job: Job, action: str, *, cascade: bool = True):
        """Steer a job; import deferred to break lifecycle ↔ worker cycle."""
        # deferred: circular import (lifecycle ↔ worker)
        from peon.projects.worker import apply_directive

        return apply_directive(job, action, cascade=cascade)


    @classmethod
    def mark_objectives_for_skill(cls, 
        project: Project | None,
        skill: str,
        status: str,
        *,
        reason: str = "",
    ) -> int:
        """Update project objectives whose skill_suggestion matches ``skill``."""
        if project is None:
            return 0
        name = (skill or "").strip()
        if not name:
            return 0
        qs = project.objectives.filter(skill_suggestion=name).exclude(
            status=ObjectiveStatus.CANCELLED
        )
        now = dj_tz.now()
        n = 0
        for obj in qs:
            fields = ["status", "updated_at"]
            obj.status = status
            if status == ObjectiveStatus.IN_PROGRESS:
                if obj.started_at is None:
                    obj.started_at = now
                    fields.append("started_at")
            elif status == ObjectiveStatus.BLOCKED:
                obj.blocked_reason = (reason or "").strip()[:2000]
                fields.append("blocked_reason")
            elif status == ObjectiveStatus.COMPLETED:
                obj.blocked_reason = ""
                fields.append("blocked_reason")
                obj.completed_at = now
                fields.append("completed_at")
                if obj.started_at is None:
                    obj.started_at = now
                    fields.append("started_at")
            obj.save(update_fields=fields)
            n += 1
        return n


    @classmethod
    def cancel_open_objectives_for_skills(cls, 
        project: Project | None, skills: Iterable[str]
    ) -> int:
        """Mark still-open objectives for the given skills as cancelled."""
        if project is None:
            return 0
        names = {str(s).strip() for s in skills if str(s).strip()}
        if not names:
            return 0
        n = 0
        for obj in project.objectives.filter(
            skill_suggestion__in=names, status__in=_OPEN_OBJECTIVE
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

        # deferred: avoid import cycle at module load
        from peon.projects.objectives import ObjectiveScheduler

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
        return project


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
    def delete_job(cls, job: Job) -> None:
        """Cancel if running, then delete the job row (cascades stream messages)."""
        if job.status not in _TERMINAL_JOB:
            cls._apply_directive(job, "kill")
        job.delete()


    @classmethod
    def cascade_steer_children(cls, job: Job, action: str, *, note: str = "") -> int:
        """pause | kill non-terminal child Jobs (depth-first). Thin A4b orphan policy."""
        action = (action or "").strip().lower()
        if action not in {"pause", "kill"}:
            return 0
        n = 0
        for child in list(job.children.exclude(status__in=_TERMINAL_JOB)):
            n += cls.cascade_steer_children(child, action, note=note)
            before = child.status
            cls._apply_directive(child, action, cascade=False)
            child.refresh_from_db()
            if child.status != before:
                n += 1
                if note and child.status in {JobStatus.PAUSED, JobStatus.CANCELLED}:
                    child.error = note[:2000]
                    child.save(update_fields=["error", "updated_at"])
        return n


    @classmethod
    def project_accepts_work(cls, project: Project | None) -> bool:
        if project is None:
            return True
        return project.status == ProjectStatus.ACTIVE

    @classmethod
    def project_allows_operator(cls, project: Project | None) -> None:
        """Raise if the project cannot accept operator actions (re-run / replan)."""
        if project is None:
            return
        if project.status == ProjectStatus.CANCELLED:
            raise RuntimeError("Project is cancelled — cannot run operator actions")
        if project.status == ProjectStatus.PAUSED:
            raise RuntimeError("Project is paused — resume before running operator actions")

    @classmethod
    def queue_job(cls, job: Job) -> Job:
        """Reset a job to PENDING, enqueue worker, and reopen project if finished."""
        job.status = JobStatus.PENDING
        job.error = ""
        job.result = ""
        job.started_at = None
        job.completed_at = None
        job.save(
            update_fields=[
                "status",
                "error",
                "result",
                "started_at",
                "completed_at",
                "updated_at",
            ]
        )
        enqueue_job(str(job.id))
        if job.project_id:
            cls.reconcile_project_status(job.project)
        return job

    @classmethod
    def rerun_job(cls, job: Job, *, command: str = "") -> Job:
        """Re-queue one job with an optional operator command (no project replan).

        Agent-emitted CLI/tool commands (``nmap …``, ``run_skill_script(..., command=…)``)
        are stored on ``objective.commands`` and as a FOLLOWUP — they do **not** replace
        the objective brief in ``job.description``. Brief-like text still updates
        description (legacy). Allowed on finished projects; blocked when paused/cancelled.
        """
        from peon.projects.agent_commands import (
            looks_like_agent_command,
            unwrap_skill_command,
        )
        from peon.projects.models import JobDirectiveKind

        cls.project_allows_operator(job.project)
        cmd = (command or "").strip()
        if cmd:
            obj = getattr(job, "objective", None)
            if looks_like_agent_command(cmd):
                shell = unwrap_skill_command(cmd)[:8000]
                if obj is not None:
                    prior = [
                        str(c).strip()
                        for c in (obj.commands or [])
                        if str(c).strip()
                        and str(c).strip() != shell
                        and str(c).strip() != cmd
                    ]
                    obj.commands = [shell, *prior][:20]
                    obj.save(update_fields=["commands", "updated_at"])
                cls.enqueue_job_directive(
                    job,
                    "OPERATOR RE-RUN — execute this exact command under current RoE "
                    f"(re-scan / re-run is requested):\n{shell}",
                    kind=JobDirectiveKind.FOLLOWUP,
                )
            else:
                job.description = cmd[:8000]
                job.save(update_fields=["description", "updated_at"])
                if obj is not None:
                    prior = [
                        str(c).strip()
                        for c in (obj.commands or [])
                        if str(c).strip() and str(c).strip() != cmd
                    ]
                    obj.commands = [cmd, *prior][:20]
                    obj.save(update_fields=["commands", "updated_at"])
                cls.enqueue_job_directive(
                    job,
                    "OPERATOR RE-RUN — execute under current RoE using this command/"
                    f"approach:\n{cmd}",
                    kind=JobDirectiveKind.FOLLOWUP,
                )
        return cls.queue_job(job)

    @classmethod
    def replan_from_prompt(cls, project: Project, message: str) -> dict:
        """Live-feed / operator prompt → full project replan (supersedes open objectives)."""
        from peon.projects.services import PlanningService

        text = (message or "").strip()
        if not text:
            raise ValueError("Message is required")
        cls.project_allows_operator(project)
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
    def enqueue_job_directive(
        cls,
        job: Job,
        content: str,
        *,
        kind: str = "steer",
    ):
        """Queue an operator instruction for a job's agent loop."""
        from peon.projects.models import JobDirective, JobDirectiveKind

        text = (content or "").strip()
        if not text:
            raise ValueError("Directive content is required")
        k = (kind or "steer").strip().lower()
        if k not in {c.value for c in JobDirectiveKind}:
            k = JobDirectiveKind.STEER
        return JobDirective.objects.create(job=job, kind=k, content=text)

    @classmethod
    def route_operator_instruction(cls, project: Project, message: str) -> dict:
        """Steer active root jobs, or re-queue the latest finished job with a follow-up.

        Returns a dict: mode, job_ids, primary_job_id.
        """
        from peon.projects.models import JobDirectiveKind
        from peon.projects.streaming import record_stream_message

        text = (message or "").strip()
        if not text:
            raise ValueError("Message is required")
        cls.project_allows_operator(project)

        live = list(
            project.jobs.filter(
                parent__isnull=True,
                status__in={JobStatus.RUNNING, JobStatus.PENDING, JobStatus.PAUSED},
            ).order_by("-updated_at")
        )
        # Prefer long-lived / currently running roots.
        running = [j for j in live if j.status == JobStatus.RUNNING]
        targets = running or live

        steered: list[str] = []
        mode = "steer"
        primary = None

        note = (
            "OPERATOR PROJECT INSTRUCTION — revise the plan and continue under RoE:\n"
            f"{text}"
        )

        if targets:
            primary = targets[0]
            for job in targets:
                cls.enqueue_job_directive(job, note, kind=JobDirectiveKind.STEER)
                steered.append(str(job.id))
        else:
            finished = (
                project.jobs.filter(
                    parent__isnull=True,
                    status__in=TERMINAL_JOB_STATUSES,
                )
                .order_by("-updated_at")
                .first()
            )
            if finished is None:
                raise RuntimeError(
                    "No jobs on this project yet — start a job, then send instructions"
                )
            primary = finished
            mode = "continue"
            cls.enqueue_job_directive(
                finished, note, kind=JobDirectiveKind.FOLLOWUP
            )
            cls.queue_job(finished)
            steered.append(str(finished.id))

        record_stream_message(
            str(primary.id),
            "log",
            text,
            {
                "event": "project_instruction",
                "role": "user",
                "project_id": str(project.id),
            },
        )
        record_stream_message(
            str(primary.id),
            "log",
            f"Project instruction routed to {len(steered)} job(s) ({mode}).",
            {
                "event": "project_instruction_acked",
                "role": "assistant",
                "project_id": str(project.id),
            },
        )
        return {
            "mode": mode,
            "job_ids": steered,
            "primary_job_id": str(primary.id),
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
        n = 0
        for project in cls._projects(ids):
            if project.status == ProjectStatus.ACTIVE:
                cls.pause_project(project)
                n += 1
        return n


    @classmethod
    def bulk_resume_projects(cls, ids: Iterable[str]) -> int:
        n = 0
        for project in cls._projects(ids):
            if project.status == ProjectStatus.PAUSED:
                cls.resume_project(project)
                n += 1
        return n


    @classmethod
    def bulk_delete_projects(cls, ids: Iterable[str]) -> int:
        n = 0
        for project in cls._projects(ids):
            cls.delete_project(project)
            n += 1
        return n


    @classmethod
    def _jobs_for_project(cls, project: Project, ids: Iterable[str]) -> list[Job]:
        wanted = [str(x).strip() for x in ids if str(x).strip()]
        if not wanted:
            return []
        by_id = {
            str(j.id): j
            for j in Job.objects.filter(project=project, pk__in=wanted)
        }
        return [by_id[i] for i in wanted if i in by_id]


    @classmethod
    def bulk_steer_jobs(cls, project: Project, ids: Iterable[str], action: str) -> int:
        """pause | resume | kill | queue selected jobs. Returns count acted on."""
        action = (action or "").strip().lower()
        jobs = cls._jobs_for_project(project, ids)
        n = 0
        if action == "queue":
            if not cls.project_accepts_work(project):
                return 0
            for job in jobs:
                if job.status in _TERMINAL_JOB or job.status == JobStatus.PENDING:
                    cls.queue_job(job)
                    n += 1
            return n

        if action == "resume" and not cls.project_accepts_work(project):
            return 0

        for job in jobs:
            if action == "pause" and job.status in {
                JobStatus.RUNNING,
                JobStatus.PENDING,
            }:
                cls._apply_directive(job, "pause")
                n += 1
            elif action == "resume" and job.status == JobStatus.PAUSED:
                cls._apply_directive(job, "resume")
                n += 1
            elif action == "kill" and job.status not in _TERMINAL_JOB:
                cls._apply_directive(job, "kill")
                n += 1
        return n


    @classmethod
    def bulk_delete_jobs(cls, project: Project, ids: Iterable[str]) -> int:
        n = 0
        for job in cls._jobs_for_project(project, ids):
            cls.delete_job(job)
            n += 1
        return n
