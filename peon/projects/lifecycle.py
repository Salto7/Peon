"""Project / Job operator lifecycle: pause, resume, delete, steer, bulk."""

from __future__ import annotations

from collections.abc import Iterable

from django.db import transaction
from django.utils import timezone as dj_tz

from orchestrator.utils.commands import as_shell_cli, looks_like_agent_command
from peon.projects.models import (
    TERMINAL_JOB_STATUSES,
    Job,
    JobDirective,
    JobDirectiveKind,
    JobStatus,
    ObjectiveStatus,
    Project,
    ProjectStatus,
)
from peon.projects.objectives import ObjectiveScheduler
from peon.projects.planning import PlanningService
from peon.projects.job_claim import emit
from peon.projects.streaming import record_stream_message
from peon.projects.tasks import enqueue_job, enqueue_sandbox_cleanup
from peon.projects.workspaces import remove_project_workspace

_TERMINAL_JOB = TERMINAL_JOB_STATUSES
_OPEN_OBJECTIVE = frozenset(
    {ObjectiveStatus.PENDING, ObjectiveStatus.IN_PROGRESS, ObjectiveStatus.BLOCKED}
)
_PROJECT_PAUSE_NOTE = "Paused with project"


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


def apply_directive(job: Job, action: str, *, cascade: bool = True) -> Job:
    """Operator steer: pause | resume | kill."""
    action = (action or "").strip().lower()
    if job.status in TERMINAL_JOB_STATUSES and action != "resume":
        return job

    if action == "pause":
        if job.status in {JobStatus.RUNNING, JobStatus.PENDING}:
            job.status = JobStatus.PAUSED
            job.error = "Paused by operator"
            job.save(update_fields=["status", "error", "updated_at"])
            emit(job, "status", "Paused by operator")
            if cascade:
                LifecycleJobsMixin.cascade_steer_children(
                    job, "pause", note="Paused with parent job"
                )
    elif action == "resume":
        if job.status == JobStatus.PAUSED:
            job.status = JobStatus.PENDING
            job.error = ""
            job.completed_at = None
            job.resume_from_checkpoint = True
            job.save(
                update_fields=[
                    "status",
                    "error",
                    "completed_at",
                    "resume_from_checkpoint",
                    "updated_at",
                ]
            )
            emit(job, "status", "Resumed → pending (checkpoint)")
            enqueue_job(str(job.id))
    elif action == "kill":
        if job.status not in TERMINAL_JOB_STATUSES:
            job.status = JobStatus.CANCELLED
            job.error = "Cancelled by operator"
            job.completed_at = dj_tz.now()
            job.save(update_fields=["status", "error", "completed_at", "updated_at"])
            emit(job, "status", "Cancelled by operator")
            if cascade:
                LifecycleJobsMixin.cascade_steer_children(
                    job, "kill", note="Cancelled with parent job"
                )
            if job.project_id:
                if job.objective_id and job.objective is not None:
                    ObjectiveScheduler().mark(
                        job.objective, ObjectiveStatus.CANCELLED
                    )
                else:
                    LifecycleStatusMixin.cancel_open_objectives_for_roles(
                        job.project, job.role_ids or []
                    )
                LifecycleStatusMixin.reconcile_project_status(job.project)
    return job


class LifecycleJobsMixin:
    """Job-scoped operator lifecycle."""

    @classmethod
    def _apply_directive(cls, job: Job, action: str, *, cascade: bool = True) -> Job:
        return apply_directive(job, action, cascade=cascade)

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
    def _push_objective_command(cls, job: Job, command: str) -> None:
        """Prepend a command onto the objective's command list (deduped, capped)."""
        obj = getattr(job, "objective", None)
        if obj is None:
            return
        cmd = (command or "").strip()
        if not cmd:
            return
        prior = [
            str(c).strip()
            for c in (obj.commands or [])
            if str(c).strip() and str(c).strip() != cmd
        ]
        obj.commands = [cmd, *prior][:20]
        obj.save(update_fields=["commands", "updated_at"])

    @classmethod
    def rerun_job(cls, job: Job, *, command: str = "") -> Job:
        """Re-queue one job with an optional operator command (no project replan).

        Agent-emitted catalog CLIs / capability tool calls (``command=…``)
        are stored on ``objective.commands`` and as a FOLLOWUP — they do **not** replace
        the objective brief in ``job.description``. Brief-like text still updates
        description (legacy). Allowed on finished projects; blocked when paused/cancelled.
        """
        cls.project_allows_operator(job.project)
        cmd = (command or "").strip()
        if cmd:
            shell = as_shell_cli(cmd)
            if shell:
                cls._push_objective_command(job, shell)
                # Also drop the raw form if it differed (shell normalized).
                note = (
                    "OPERATOR RE-RUN — execute this exact command under current "
                    "Rules of Engagement (re-scan / re-run is requested):\n"
                    f"{shell}"
                )
            elif looks_like_agent_command(cmd):
                cls._push_objective_command(job, cmd)
                note = (
                    "OPERATOR RE-RUN — honor this tool instruction under current "
                    f"Rules of Engagement:\n{cmd}"
                )
            else:
                job.description = cmd[:8000]
                job.save(update_fields=["description", "updated_at"])
                cls._push_objective_command(job, cmd)
                note = (
                    "OPERATOR RE-RUN — execute under current Rules of Engagement "
                    f"using this command/approach:\n{cmd}"
                )
            cls.enqueue_job_directive(job, note, kind=JobDirectiveKind.FOLLOWUP)
        return cls.queue_job(job)

    @classmethod
    def enqueue_job_directive(
        cls,
        job: Job,
        content: str,
        *,
        kind: str = "steer",
    ):
        """Queue an operator instruction for a job's agent loop."""
        text = (content or "").strip()
        if not text:
            raise ValueError("Directive content is required")
        k = (kind or "steer").strip().lower()
        if k not in {c.value for c in JobDirectiveKind}:
            k = JobDirectiveKind.STEER
        return JobDirective.objects.create(job=job, kind=k, content=text)

    @classmethod
    def route_operator_instruction(
        cls,
        project: Project,
        message: str,
        *,
        job_id: str | None = None,
        record_stream: bool = True,
    ) -> dict:
        """Inject an operator instruction into running objective jobs / agents.

        Prefers:
        1. Explicit ``job_id`` (and its non-terminal children)
        2. Jobs linked to in-progress objectives (running/pending)
        3. All running root jobs

        Raises RuntimeError when nothing is live — callers should replan for
        new work instead of silently FOLLOWUP-ing the last finished job.

        When ``record_stream`` is False, the caller owns chat/feed lines
        (console chat already records user + assistant replies).
        """
        text = (message or "").strip()
        if not text:
            raise ValueError("Message is required")
        cls.project_allows_operator(project)

        note = (
            "OPERATOR INSTRUCTION — revise your approach and continue under Rules of Engagement:\n"
            f"{text}"
        )

        targets: list[Job] = []
        mode = "instruct"

        if job_id:
            selected = (
                project.jobs.filter(pk=str(job_id).strip())
                .exclude(status__in=TERMINAL_JOB_STATUSES)
                .first()
            )
            if selected is None:
                raise RuntimeError("Selected agent is not running (or not on this project)")
            targets = [selected]
            # Also steer non-terminal children of the selected agent.
            targets.extend(
                list(
                    selected.children.exclude(status__in=TERMINAL_JOB_STATUSES).order_by(
                        "created_at"
                    )
                )
            )
            mode = "instruct_selected"
        else:
            in_progress = list(
                project.objectives.filter(status=ObjectiveStatus.IN_PROGRESS).values_list(
                    "id", flat=True
                )
            )
            if in_progress:
                targets = list(
                    project.jobs.filter(
                        objective_id__in=in_progress,
                        status__in={
                            JobStatus.RUNNING,
                            JobStatus.PENDING,
                            JobStatus.PAUSED,
                        },
                    ).order_by("-updated_at")
                )
            if not targets:
                running = list(
                    project.jobs.filter(
                        parent__isnull=True,
                        status=JobStatus.RUNNING,
                    ).order_by("-updated_at")
                )
                live = list(
                    project.jobs.filter(
                        parent__isnull=True,
                        status__in={
                            JobStatus.RUNNING,
                            JobStatus.PENDING,
                            JobStatus.PAUSED,
                        },
                    ).order_by("-updated_at")
                )
                targets = running or live
                # Include active children of those roots.
                extra: list[Job] = []
                for root in targets:
                    extra.extend(
                        list(
                            root.children.exclude(
                                status__in=TERMINAL_JOB_STATUSES
                            ).order_by("created_at")
                        )
                    )
                # Dedupe while preserving order.
                seen: set[str] = {str(j.id) for j in targets}
                for j in extra:
                    if str(j.id) not in seen:
                        targets.append(j)
                        seen.add(str(j.id))

        steered: list[str] = []
        primary = None

        if targets:
            primary = targets[0]
            for job in targets:
                cls.enqueue_job_directive(job, note, kind=JobDirectiveKind.STEER)
                steered.append(str(job.id))
        else:
            # No live agents — refuse silent FOLLOWUP on the last finished job.
            # New scans / checks belong in replan (new objectives), not re-running
            # the report analyzer or whatever finished last.
            raise RuntimeError(
                "No running agents to instruct — use Chat or Replan for new work "
                "(e.g. rescan a port) instead of re-running the last finished job"
            )

        if record_stream:
            record_stream_message(
                str(primary.id),
                "log",
                text,
                {
                    "event": "project_instruction",
                    "role": "user",
                    "tag": "you",
                    "project_id": str(project.id),
                },
            )
            record_stream_message(
                str(primary.id),
                "log",
                f"Instruction injected into {len(steered)} agent(s) ({mode}).",
                {
                    "event": "project_instruction_acked",
                    "role": "assistant",
                    "tag": "steer",
                    "project_id": str(project.id),
                },
            )
        return {
            "mode": mode,
            "job_ids": steered,
            "primary_job_id": str(primary.id),
        }

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


__all__ = ["ProjectLifecycle", "apply_directive"]


class ProjectLifecycle(LifecycleStatusMixin, LifecycleJobsMixin, LifecycleProjectsMixin):
    """Project / Job operator lifecycle: pause, resume, delete (single + bulk)."""
