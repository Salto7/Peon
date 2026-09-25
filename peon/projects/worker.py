"""Claim and run Jobs via orchestrator.agent; emit StreamMessage lines."""

from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone as dj_tz

from orchestrator.utils.job_env import JobEnv
from orchestrator.sandbox import SandboxSession
from orchestrator.skills.execute import LocalSkillExecutor, SkillExecutionDispatcher
from orchestrator.tools.catalog import CatalogProvisioner
from peon.projects.models import (
    TERMINAL_JOB_STATUSES,
    Job,
    JobStatus,
    ObjectiveStatus,
    ProjectStatus,
)
from peon.projects.sandbox import ProjectSandbox
from peon.projects.streaming import emit_job_stream
from peon.projects.targets import provision_project_roe
from peon.projects.workspaces import resolve_job_workspace


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


def claim_next_job() -> Job | None:
    """Atomically move one PENDING job to RUNNING.

    Skips paused/cancelled projects, children whose parent is stopped, and jobs
    that would exceed parallel project / per-project agent caps.
    """
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
    from peon.projects.runtime_settings import PeonSettings

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


def _emit(job: Job, message_type: str, content: str, **meta) -> None:
    emit_job_stream(job, message_type, content, meta or None, swallow_errors=True)

def _roe_blocks(job: Job) -> str | None:
    """Fail-closed for active probe skills with empty in_scope (type ignored)."""
    if job.project_id is None:
        return None
    from peon.projects.targets import roe_block_reason

    project = job.project
    roe, _ = provision_project_roe(
        project,
        texts=[job.description or "", job.title or "", project.summary or ""],
    )
    names = [str(n).strip() for n in (job.skill_names or []) if str(n).strip()]
    if not names and job.objective_id and job.objective:
        hint = (job.objective.skill_suggestion or "").strip()
        if hint:
            names = [hint]
    return roe_block_reason(names, roe.in_scope)


def _targets_json_for_job(job: Job) -> tuple[str, str, str]:
    """Return (in_scope_json, exclusions_json, seed_json)."""
    from peon.projects.targets import coerce_targets

    if job.project_id is None:
        return "[]", "[]", "[]"
    roe = getattr(job.project, "roe", None)
    scope = coerce_targets(getattr(roe, "in_scope", None) if roe else None)
    excl = coerce_targets(getattr(roe, "exclusions", None) if roe else None)
    seed = coerce_targets(getattr(roe, "seed", None) if roe else None)
    return json.dumps(scope), json.dumps(excl), json.dumps(seed)


def ensure_skill_executor() -> SkillExecutionDispatcher:
    """Register LocalSkillExecutor once (Docker sandbox) for agent tool runs."""
    dispatcher = SkillExecutionDispatcher.shared()
    local = LocalSkillExecutor.shared()
    if not any(isinstance(e, LocalSkillExecutor) for e in dispatcher._executors):
        dispatcher.register(local)
    return dispatcher


def _finish(job: Job, *, status: str, error: str = "", result: str | None = None) -> Job:
    # deferred: circular import (lifecycle ↔ worker)
    from peon.projects.lifecycle import ProjectLifecycle
    from peon.projects.objectives import ObjectiveScheduler

    job.status = status
    job.error = error
    if result is not None:
        job.result = result
    job.completed_at = dj_tz.now()
    job.save(
        update_fields=["status", "error", "result", "completed_at", "updated_at"]
    )

    sched = ObjectiveScheduler()
    if job.project_id and job.objective_id:
        obj = job.objective
        if obj is not None:
            if status == JobStatus.COMPLETED:
                sched.mark(obj, ObjectiveStatus.COMPLETED)
            elif status == JobStatus.FAILED:
                if obj.status in {
                    ObjectiveStatus.PENDING,
                    ObjectiveStatus.IN_PROGRESS,
                }:
                    sched.mark(
                        obj,
                        ObjectiveStatus.BLOCKED,
                        reason=(error or "Job failed")[:2000],
                    )
            elif status == JobStatus.CANCELLED:
                if obj.status in {
                    ObjectiveStatus.PENDING,
                    ObjectiveStatus.IN_PROGRESS,
                    ObjectiveStatus.BLOCKED,
                }:
                    sched.mark(obj, ObjectiveStatus.CANCELLED)
    elif job.project_id and status == JobStatus.CANCELLED:
        ProjectLifecycle.cancel_open_objectives_for_skills(job.project, job.skill_names or [])

    if error:
        _emit(job, "error", error)
    _emit(job, "status", f"Job → {status}", job_status=status)

    if job.project_id and status == JobStatus.COMPLETED:
        try:
            nxt = ObjectiveScheduler().enqueue_next(job.project)
            if nxt is not None:
                _emit(job, "log", f"Queued next objective job {nxt.id}")
        except Exception as exc:
            _emit(job, "error", f"Failed to queue next objective: {exc}")

    if job.project_id:
        ProjectLifecycle.reconcile_project_status(job.project)

    if status in TERMINAL_JOB_STATUSES and job.children.exists():
        from peon.projects.lifecycle import ProjectLifecycle

        ProjectLifecycle.cascade_steer_children(
            job, "kill", note=f"Orphaned: parent job → {status}"
        )
    return job


def _steer_stop(job: Job, lines: list[str]) -> Job | None:
    """If operator cancelled/paused mid-run, persist and return the job; else None."""
    job.refresh_from_db()
    joined = "\n\n".join(lines)
    if job.status == JobStatus.CANCELLED:
        return _finish(
            job,
            status=JobStatus.CANCELLED,
            error=job.error or "Cancelled",
            result=joined or None,
        )
    if job.status == JobStatus.PAUSED:
        job.result = joined
        job.save(update_fields=["result", "updated_at"])
        return job
    return None


def _bind_job_env(job: Job, ws: Path):
    """Bind job-scoped ContextVar env (no process-global ORCHESTRATOR_* races)."""
    root = str(getattr(settings, "PROJECT_WORKSPACES_DIR", "") or ws.parent)
    scope_json, excl_json, seed_json = _targets_json_for_job(job)
    env: dict[str, str] = {
        "ORCHESTRATOR_JOB_ID": str(job.id),
        "ORCHESTRATOR_WORKSPACE": str(ws),
        "PROJECT_WORKSPACES_DIR": root,
        "ORCHESTRATOR_STREAM_SOCKET": str(
            getattr(settings, "STREAM_SOCKET_PATH", "") or ""
        ),
        "ORCHESTRATOR_RPC_SOCKET": str(
            getattr(settings, "RPC_SOCKET_PATH", "") or ""
        ),
        "ORCHESTRATOR_RPC_TOKEN": str(getattr(settings, "RPC_TOKEN", "") or ""),
        "SKILLS_DIR": str(settings.SKILLS_DIR),
        "SANDBOX_SKILLS_PATH": str(
            getattr(settings, "SANDBOX_SKILLS_PATH", "") or settings.SKILLS_DIR
        ),
        "TOOLS_CATALOG_DIR": str(
            getattr(settings, "TOOLS_CATALOG_DIR", "")
            or (Path(settings.SKILLS_DIR).parent / "tools" / "catalog")
        ),
        "ORCHESTRATOR_IN_SCOPE": scope_json,
        "ORCHESTRATOR_EXCLUSIONS": excl_json,
        "ORCHESTRATOR_SEED": seed_json,
    }
    if job.project_id:
        env["ORCHESTRATOR_PROJECT_ID"] = str(job.project_id)
    brief = (job.description or job.title or "").strip()
    if brief:
        env["ORCHESTRATOR_JOB_BRIEF"] = brief
    skill_cmd = _operator_skill_command(job)
    if skill_cmd:
        env["ORCHESTRATOR_SKILL_COMMAND"] = skill_cmd
    return JobEnv.bind(env)


def _operator_skill_command(job: Job) -> str:
    """CLI for BinaryRunner: objective operator command, else one-line non-brief description."""
    from peon.projects.agent_commands import (
        looks_like_agent_command,
        unwrap_skill_command,
    )

    obj = getattr(job, "objective", None)
    if obj is not None:
        for raw in obj.commands or []:
            text = str(raw or "").strip()
            if looks_like_agent_command(text):
                return unwrap_skill_command(text)[:8000]
    brief = (job.description or "").strip()
    if brief and "\n" not in brief and looks_like_agent_command(brief):
        return unwrap_skill_command(brief)[:8000]
    return ""


def _run_analyzer_only(job: Job, ws: Path) -> Job:
    """Deterministic report synth — no LLM loop (analyzer bookend)."""
    from peon.projects.analysis import synthesize_report_for_job

    stopped = _steer_stop(job, [])
    if stopped is not None:
        return stopped
    try:
        report = synthesize_report_for_job(job, ws)
        chunk = f"## analyzer\nok=True\nWrote {report}"
        _emit(job, "result", str(report), skill="analyzer")
        return _finish(job, status=JobStatus.COMPLETED, error="", result=chunk)
    except Exception as exc:
        _emit(job, "error", str(exc), skill="analyzer")
        return _finish(
            job,
            status=JobStatus.FAILED,
            error=str(exc)[:2000],
            result=f"## analyzer\nok=False\n{exc}",
        )


def run_job(job: Job) -> Job:
    """Provision sandbox, then run the Job agent (single executor)."""
    from orchestrator.utils.job_env import JobEnv
    from peon.projects.objectives import ObjectiveScheduler

    block = _roe_blocks(job)
    if block:
        return _finish(job, status=JobStatus.FAILED, error=block, result="")

    names = [str(n).strip() for n in (job.skill_names or []) if str(n).strip()]
    if not names and job.objective_id and job.objective:
        names = ObjectiveScheduler().skills_for(job.objective)
        job.skill_names = names
        job.save(update_fields=["skill_names", "updated_at"])
    if not names:
        return _finish(job, status=JobStatus.FAILED, error="No skill_names on Job")

    if job.objective_id and job.objective is not None:
        obj = job.objective
        if obj.status == ObjectiveStatus.CANCELLED:
            return _finish(
                job,
                status=JobStatus.CANCELLED,
                error="Objective cancelled",
                result="",
            )
        if not ObjectiveScheduler().dependencies_met(obj):
            why = f"OBJ-{obj.seq} dependencies not met"
            ObjectiveScheduler().mark(obj, ObjectiveStatus.BLOCKED, reason=why)
            return _finish(job, status=JobStatus.FAILED, error=why, result="")
        ObjectiveScheduler().mark(obj, ObjectiveStatus.IN_PROGRESS)

    _emit(
        job,
        "status",
        f"Running skills: {', '.join(names)}",
        job_status=JobStatus.RUNNING,
    )

    ws = resolve_job_workspace(job)
    token = _bind_job_env(job, ws)
    try:
        try:
            project_id = str(job.project_id or "")
            sb = ProjectSandbox.provision(
                project_id,
                workspace=ws,
                skills_dir=Path(settings.SKILLS_DIR),
                tools_dir=Path(getattr(settings, "TOOLS_CATALOG_DIR", "") or ""),
            )
            _emit(
                job,
                "log",
                f"sandbox {sb.mode}:{sb.name} ({sb.action})"
                + (f" base_cmds={len(sb.base_commands)}" if sb.base_commands else ""),
            )
            provision = CatalogProvisioner.shared().provision(names, workspace=ws)
            if provision.installed:
                _emit(job, "log", "provisioned: " + ", ".join(provision.installed))
            if provision.verified:
                _emit(job, "log", "verified CLIs: " + ", ".join(provision.verified))
            if provision.errors:
                _emit(job, "error", "CLI provision: " + "; ".join(provision.errors))
        except Exception as exc:
            _emit(job, "error", f"sandbox/provision error: {exc}")

        ensure_skill_executor()

        stopped = _steer_stop(job, [])
        if stopped is not None:
            return stopped

        # Bookend: analyzer alone is deterministic report synth (no LangGraph).
        if names == ["analyzer"]:
            return _run_analyzer_only(job, ws)

        return run_job_via_agent(job, skill_names=names, workspace=str(ws))
    finally:
        JobEnv.reset(token)
        SandboxSession.reset()


def run_job_via_agent(job: Job, *, skill_names: list[str], workspace: str) -> Job:
    """Execute one Job through orchestrator.agent.run (sandbox already bound)."""
    from orchestrator.agent import AgentRunContext, run_agent
    from peon.projects.agent_bridge import JobAgentBridge, agent_run_config

    cfg = agent_run_config()
    depth = 1
    parent = job.parent
    while parent is not None:
        depth += 1
        parent = parent.parent

    ctx = AgentRunContext(
        job_id=str(job.id),
        project_id=str(job.project_id or ""),
        parent_job_id=str(job.parent_id or ""),
        workspace=workspace,
        skill_names=list(skill_names),
        brief=(job.description or job.title or "").strip(),
        depth=depth,
        ports=JobAgentBridge(job),
    )
    _emit(job, "status", f"Agent runtime (max_iter={cfg.max_iterations})")
    result = run_agent(ctx, cfg)
    try:
        from peon.projects.findings import ingest_workspace_findings

        n = ingest_workspace_findings(job, Path(workspace))
        if n:
            _emit(job, "log", f"ingested {n} finding(s) from queue")
    except Exception as exc:
        _emit(job, "error", f"finding ingest: {exc}")

    if result.ok:
        return _finish(
            job,
            status=JobStatus.COMPLETED,
            error="",
            result=result.output or "",
        )
    return _finish(
        job,
        status=JobStatus.FAILED,
        error=(result.error or "agent failed")[:2000],
        result=result.output or "",
    )


def process_one() -> Job | None:
    """Claim one PENDING job and run it. Returns the job, or None if idle."""
    job = claim_next_job()
    if job is None:
        return None
    try:
        return run_job(job)
    except Exception as exc:
        return _finish(job, status=JobStatus.FAILED, error=str(exc))


def apply_directive(job: Job, action: str, *, cascade: bool = True) -> Job:
    """Operator steer: pause | resume | kill.

    When ``cascade`` is True (default), pause/kill also steers non-terminal children.
    """
    from peon.projects.lifecycle import ProjectLifecycle
    from peon.projects.objectives import ObjectiveScheduler
    from peon.projects.tasks import enqueue_job

    action = (action or "").strip().lower()
    if job.status in TERMINAL_JOB_STATUSES and action != "resume":
        return job

    if action == "pause":
        if job.status in {JobStatus.RUNNING, JobStatus.PENDING}:
            job.status = JobStatus.PAUSED
            job.error = "Paused by operator"
            job.save(update_fields=["status", "error", "updated_at"])
            _emit(job, "status", "Paused by operator")
            if cascade:
                ProjectLifecycle.cascade_steer_children(
                    job, "pause", note="Paused with parent job"
                )
    elif action == "resume":
        if job.status == JobStatus.PAUSED:
            job.status = JobStatus.PENDING
            job.error = ""
            job.completed_at = None
            job.save(update_fields=["status", "error", "completed_at", "updated_at"])
            _emit(job, "status", "Resumed → pending")
            enqueue_job(str(job.id))
    elif action == "kill":
        if job.status not in TERMINAL_JOB_STATUSES:
            job.status = JobStatus.CANCELLED
            job.error = "Cancelled by operator"
            job.completed_at = dj_tz.now()
            job.save(update_fields=["status", "error", "completed_at", "updated_at"])
            _emit(job, "status", "Cancelled by operator")
            if cascade:
                ProjectLifecycle.cascade_steer_children(
                    job, "kill", note="Cancelled with parent job"
                )
            if job.project_id:
                if job.objective_id and job.objective is not None:
                    ObjectiveScheduler().mark(
                        job.objective, ObjectiveStatus.CANCELLED
                    )
                else:
                    ProjectLifecycle.cancel_open_objectives_for_skills(
                        job.project, job.skill_names or []
                    )
                ProjectLifecycle.reconcile_project_status(job.project)
    return job
