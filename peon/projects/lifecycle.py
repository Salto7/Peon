"""Project / Job operator lifecycle: pause, resume, delete, steer, bulk."""

from __future__ import annotations

import logging

from collections.abc import Iterable

from django.db import transaction
from django.utils import timezone as dj_tz

from orchestrator.utils.commands import as_shell_cli, looks_like_agent_command
from peon.projects.models import (
    FINISHED_PROJECT_STATUSES,
    INACTIVE_PROJECT_STATUSES,
    TERMINAL_JOB_STATUSES,
    TERMINAL_PROJECT_STATUSES,
    Job,
    JobDirective,
    JobDirectiveKind,
    JobStatus,
    ObjectiveStatus,
    Project,
    ProjectStatus,
)
from peon.projects.objectives import ObjectiveScheduler
from peon.projects.planning import replan_project
from peon.projects.job_claim import emit
from peon.projects.streaming import record_stream_message
from peon.projects.tasks import enqueue_job, enqueue_sandbox_cleanup
from peon.projects.workspaces import release_project_handles, remove_project_workspace

logger = logging.getLogger(__name__)

_OPEN_OBJECTIVE = frozenset(
    {ObjectiveStatus.PENDING, ObjectiveStatus.IN_PROGRESS, ObjectiveStatus.BLOCKED}
)
_PROJECT_PAUSE_NOTE = "Paused with project"


def cancel_open_objectives_for_roles(project: Project | None, role_ids: Iterable[str]
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


def reconcile_project_status(project: Project | None) -> Project | None:
    """Roll project status from jobs + objectives. No-op while paused or cancelled.

    Status is authoritative on the **platform** ``Project`` row (list/home UI).
    Engagement facts (jobs/objectives) are read from the project SQLite file.
    Saving status onto a project-DB mirror instance would leave the UI stuck
    on ``active`` after a finished run — always write ``using="default"``.
    """
    if project is None:
        return None
    pid = project.pk
    try:
        registry = Project.objects.using("default").get(pk=pid)
    except Project.DoesNotExist:
        return project
    if registry.status in INACTIVE_PROJECT_STATUSES:
        return registry

    # ProjectRelation always hits /workspaces/<id>/db.sqlite3 via pk.
    statuses = list(project.jobs.values_list("status", flat=True))
    obj_statuses = list(project.objectives.values_list("status", flat=True))
    has_ready = ObjectiveScheduler().next_ready(project) is not None
    # IDLE agents are standby, not work in flight: they keep the project open
    # only while something can still wake them (a running/queued agent, a ready
    # objective, or an operator prompt awaiting an answer).
    active_now = any(
        s not in TERMINAL_JOB_STATUSES and s != JobStatus.IDLE for s in statuses
    )
    has_idle = any(s == JobStatus.IDLE for s in statuses)
    jobs_busy = bool(
        active_now
        or (has_idle and (has_ready or int(registry.open_prompts or 0) > 0))
    )
    objs_busy = any(
        s in {ObjectiveStatus.PENDING, ObjectiveStatus.IN_PROGRESS} for s in obj_statuses
    )

    def _save_registry(*, status: str, clear_completed: bool = False) -> Project:
        fields = ["status", "updated_at"]
        registry.status = status
        if clear_completed:
            registry.completed_at = None
            fields.append("completed_at")
        elif status in TERMINAL_PROJECT_STATUSES:
            registry.completed_at = dj_tz.now()
            fields.append("completed_at")
        registry.save(using="default", update_fields=fields)
        return registry

    if jobs_busy or has_ready or (
        objs_busy and any(s == ObjectiveStatus.IN_PROGRESS for s in obj_statuses)
    ):
        if registry.status != ProjectStatus.ACTIVE:
            return _save_registry(status=ProjectStatus.ACTIVE, clear_completed=True)
        return registry

    if not statuses and not obj_statuses:
        return registry

    if has_ready:
        return registry

    from peon.projects.models import ProjectKind

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

    # Durable projects stay ACTIVE after a cycle; they never auto-finish.
    if registry.kind == ProjectKind.DURABLE and next_status in FINISHED_PROJECT_STATUSES:
        if registry.status != ProjectStatus.ACTIVE:
            _save_registry(status=ProjectStatus.ACTIVE, clear_completed=True)
        try:
            from peon.projects.schedules import rearm_completed_for_schedules

            rearm_completed_for_schedules(registry)
        except Exception as exc:
            logger.warning("rearm_completed_for_schedules failed: %s", exc, exc_info=True)
        return registry

    newly_terminal = (
        registry.status != next_status and next_status in TERMINAL_PROJECT_STATUSES
    )
    if registry.status != next_status or (
        next_status in TERMINAL_PROJECT_STATUSES and registry.completed_at is None
    ):
        _save_registry(status=next_status)
    if newly_terminal:
        try:
            from peon.projects.memory_store import record_project_digest

            record_project_digest(project, next_status)
        except Exception as exc:
            logger.warning("record_project_digest failed: %s", exc, exc_info=True)
    if next_status in TERMINAL_PROJECT_STATUSES and (registry.crew_status or "").strip():
        from peon.projects.crew_control import set_crew_status

        set_crew_status(
            registry,
            "done" if next_status == ProjectStatus.FINISHED else "stopped",
        )
    return registry


def project_accepts_work(project: Project | None) -> bool:
    if project is None:
        return True
    return project.status == ProjectStatus.ACTIVE


def project_allows_operator(project: Project | None) -> None:
    """Raise if the project cannot accept instruct / re-run (not replan/stop)."""
    if project is None:
        return
    if project.status == ProjectStatus.CANCELLED:
        raise RuntimeError("Project is cancelled — cannot run operator actions")
    if project.status == ProjectStatus.PAUSED:
        raise RuntimeError("Project is paused — resume before running operator actions")


def delete_job(job: Job) -> None:
    """Cancel if running, then delete the job row (cascades stream messages)."""
    if job.status not in TERMINAL_JOB_STATUSES:
        apply_directive(job, "kill")
    job.delete()


def cascade_steer_children(job: Job, action: str, *, note: str = "") -> int:
    """pause | kill non-terminal child Jobs (depth-first). Thin A4b orphan policy."""
    action = (action or "").strip().lower()
    if action not in {"pause", "kill"}:
        return 0
    n = 0
    for child in list(job.children.exclude(status__in=TERMINAL_JOB_STATUSES)):
        n += cascade_steer_children(child, action, note=note)
        before = child.status
        apply_directive(child, action, cascade=False)
        child.refresh_from_db()
        if child.status != before:
            n += 1
            if note and child.status in {JobStatus.PAUSED, JobStatus.CANCELLED}:
                child.error = note[:2000]
                child.save(update_fields=["error", "updated_at"])
    return n


def queue_job(job: Job) -> Job:
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
        reconcile_project_status(job.project)
    return job


def _push_objective_command(job: Job, command: str) -> None:
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


def rerun_job(job: Job, *, command: str = "") -> Job:
    """Re-queue one job with an optional operator command (no project replan).

    Agent-emitted catalog CLIs / capability tool calls (``command=…``)
    are stored on ``objective.commands`` and as a FOLLOWUP — they do **not** replace
    the objective brief in ``job.description``. Brief-like text still updates
    description (legacy). Allowed on finished projects; blocked when paused/cancelled.
    """
    project_allows_operator(job.project)
    cmd = (command or "").strip()
    if cmd:
        shell = as_shell_cli(cmd)
        if shell:
            _push_objective_command(job, shell)
            # Also drop the raw form if it differed (shell normalized).
            note = (
                "OPERATOR RE-RUN — execute this exact command under current "
                "Rules of Engagement (re-scan / re-run is requested):\n"
                f"{shell}"
            )
        elif looks_like_agent_command(cmd):
            _push_objective_command(job, cmd)
            note = (
                "OPERATOR RE-RUN — honor this tool instruction under current "
                f"Rules of Engagement:\n{cmd}"
            )
        else:
            job.description = cmd[:8000]
            job.save(update_fields=["description", "updated_at"])
            _push_objective_command(job, cmd)
            note = (
                "OPERATOR RE-RUN — execute under current Rules of Engagement "
                f"using this command/approach:\n{cmd}"
            )
        enqueue_job_directive(job, note, kind=JobDirectiveKind.FOLLOWUP)
    return queue_job(job)


def enqueue_job_directive(
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
    from peon.projects.db import ensure_project_database, project_alias_or_default

    alias = (
        ensure_project_database(job.project_id)
        if job.project_id
        else project_alias_or_default(None)
    )
    return JobDirective.objects.using(alias).create(
        job=job, kind=k, content=text
    )


def route_operator_instruction(
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
    project_allows_operator(project)

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
                        JobStatus.IDLE,
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
                        JobStatus.IDLE,
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
            enqueue_job_directive(job, note, kind=JobDirectiveKind.STEER)
            steered.append(str(job.id))
            if job.status == JobStatus.IDLE:
                # An operator instruction wakes a parked agent (unless it is
                # still gated on objective dependencies).
                from peon.projects import a2a

                if a2a.can_wake(job):
                    a2a.wake_job(job, reason="operator instruction")
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


def _jobs_for_project(project: Project, ids: Iterable[str]) -> list[Job]:
    wanted = [str(x).strip() for x in ids if str(x).strip()]
    if not wanted:
        return []
    by_id = {
        str(j.id): j
        for j in project.jobs.filter(pk__in=wanted)
    }
    return [by_id[i] for i in wanted if i in by_id]


def bulk_steer_jobs(project: Project, ids: Iterable[str], action: str) -> int:
    """pause | resume | kill | queue selected jobs. Returns count acted on."""
    action = (action or "").strip().lower()
    jobs = _jobs_for_project(project, ids)
    n = 0
    if action == "queue":
        if not project_accepts_work(project):
            return 0
        for job in jobs:
            if job.status in TERMINAL_JOB_STATUSES or job.status == JobStatus.PENDING:
                queue_job(job)
                n += 1
        return n

    if action == "resume" and not project_accepts_work(project):
        return 0

    for job in jobs:
        if action == "pause" and job.status in {
            JobStatus.RUNNING,
            JobStatus.PENDING,
        }:
            apply_directive(job, "pause")
            n += 1
        elif action == "resume" and job.status == JobStatus.PAUSED:
            apply_directive(job, "resume")
            n += 1
        elif action == "kill" and job.status not in TERMINAL_JOB_STATUSES:
            apply_directive(job, "kill")
            n += 1
    return n


def bulk_delete_jobs(project: Project, ids: Iterable[str]) -> int:
    n = 0
    for job in _jobs_for_project(project, ids):
        delete_job(job)
        n += 1
    return n


def pause_project(project: Project) -> Project:
    """Pause project and all non-terminal jobs."""
    project.status = ProjectStatus.PAUSED
    project.save(update_fields=["status", "updated_at"])
    for job in project.jobs.exclude(status__in=TERMINAL_JOB_STATUSES):
        if job.status != JobStatus.PAUSED:
            apply_directive(job, "pause")
            job.refresh_from_db()
            if job.status == JobStatus.PAUSED:
                job.error = _PROJECT_PAUSE_NOTE
                job.save(update_fields=["error", "updated_at"])
    return project


def resume_project(project: Project, *, resume_jobs: bool = True) -> Project:
    """Resume a paused/cancelled project; optionally re-queue jobs paused with it."""
    project.status = ProjectStatus.ACTIVE
    project.completed_at = None
    project.save(update_fields=["status", "completed_at", "updated_at"])
    if resume_jobs:
        for job in project.jobs.filter(status=JobStatus.PAUSED):
            if (job.error or "") == _PROJECT_PAUSE_NOTE:
                apply_directive(job, "resume")
    return project


def delete_project(project: Project) -> dict:
    """Cancel jobs, delete workspace + DB row; queue sandbox docker rm on the worker."""
    pid = str(project.id)
    title = project.title
    runtime_id = str(project.sandbox_runtime or "")
    for job in project.jobs.exclude(status__in=TERMINAL_JOB_STATUSES):
        apply_directive(job, "kill")

    release_project_handles(pid)
    disk = remove_project_workspace(pid)
    if disk.get("action") == "error":
        raise RuntimeError(
            f"Project workspace cleanup failed: {disk.get('error') or disk}"
        )
    with transaction.atomic():
        Project.objects.filter(pk=pid).delete()
    sandbox = enqueue_sandbox_cleanup(pid, runtime_id=runtime_id)
    return {
        "title": title,
        "project_id": pid,
        "sandbox": sandbox,
        "workspace": disk,
    }


def replan_from_prompt(project: Project, message: str = "") -> dict:
    """Operator replan (chat or control bar). Allowed while paused; not when cancelled."""

    if project.status == ProjectStatus.CANCELLED:
        raise RuntimeError("Project is cancelled — cannot replan")
    text = (message or "").strip() or (project.summary or project.title or "").strip()
    if not text:
        raise ValueError("Message is required")
    result = replan_project(project, description=text)
    job = result.job
    return {
        "mode": "replan",
        "job_ids": [str(job.id)] if job is not None else [],
        "primary_job_id": str(job.id) if job is not None else "",
        "objectives": len(result.objectives or []),
        "plan_preview": (result.plan_text or "")[:240],
    }


def _projects(ids: Iterable[str]) -> list[Project]:
    wanted = [str(x).strip() for x in ids if str(x).strip()]
    if not wanted:
        return []
    by_id = {str(p.id): p for p in Project.objects.filter(pk__in=wanted)}
    return [by_id[i] for i in wanted if i in by_id]


def bulk_pause_projects(ids: Iterable[str]) -> int:
    from peon.projects.crew_control import pause_project

    n = 0
    for project in _projects(ids):
        if project.status == ProjectStatus.ACTIVE:
            pause_project(project)
            n += 1
    return n


def bulk_resume_projects(ids: Iterable[str]) -> int:
    from peon.projects.crew_control import resume_project

    n = 0
    for project in _projects(ids):
        if project.status == ProjectStatus.PAUSED:
            resume_project(project)
            n += 1
    return n


def bulk_delete_projects(ids: Iterable[str]) -> int:
    n = 0
    for project in _projects(ids):
        delete_project(project)
        n += 1
    return n


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
            try:
                from peon.projects.checkpoints import write_job_checkpoint

                write_job_checkpoint(job, reason="pause")
            except Exception as exc:
                logger.warning("write_job_checkpoint failed: %s", exc, exc_info=True)
            if cascade:
                cascade_steer_children(
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
                cascade_steer_children(
                    job, "kill", note="Cancelled with parent job"
                )
            if job.project_id:
                if job.objective_id and job.objective is not None:
                    ObjectiveScheduler().mark(
                        job.objective, ObjectiveStatus.CANCELLED
                    )
                else:
                    cancel_open_objectives_for_roles(
                        job.project, job.role_ids or []
                    )
                reconcile_project_status(job.project)
    return job




