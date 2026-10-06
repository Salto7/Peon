"""Job execution and agent-command helpers."""

from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.utils import timezone as dj_tz

from agent_runtime.api import Session
from orchestrator.agent import JobScope, run_agent
from orchestrator.crew.roles.registry import RoleRegistry, engagement_bookends
from orchestrator.tools.catalog import CatalogProvisioner
from orchestrator.utils.commands import pick_agent_command
from orchestrator.utils.job_env import JobEnv
from peon.projects.agent_bridge import JobAgentBridge, agent_run_config
from peon.projects.crew_control import set_crew_status
from peon.projects.findings import ingest_workspace_findings
from peon.projects.job_claim import claim_next_job, emit
from peon.projects.lifecycle import ProjectLifecycle
from peon.projects.models import (
    TERMINAL_JOB_STATUSES,
    Job,
    JobDirective,
    JobStatus,
    ObjectiveStatus,
    ProjectStatus,
)
from peon.projects.objectives import ObjectiveScheduler
from peon.projects.roe_ops import provision_project_roe, roe_block_reason
from peon.projects.sandbox import ProjectSandbox
from peon.projects.target_shapes import coerce_targets
from peon.projects.workspaces import resolve_job_workspace


def _bookend_ids() -> tuple[str, str]:
    """``(manager_id, analyzer_id)`` from ROLE.yaml hierarchy (may be empty)."""
    return engagement_bookends()


def _roe_blocks(job: Job) -> str | None:
    """Fail-closed for active probe roles with empty in_scope (type ignored)."""
    if job.project_id is None:
        return None
    project = job.project
    roe, _ = provision_project_roe(
        project,
        texts=[job.description or "", job.title or "", project.summary or ""],
    )
    names = [str(n).strip() for n in (job.role_ids or []) if str(n).strip()]
    if not names and job.objective_id and job.objective:
        hint = (job.objective.role_id or "").strip()
        if hint:
            names = [hint]
    return roe_block_reason(names, roe.in_scope)


def _targets_json_for_job(job: Job) -> tuple[str, str, str]:
    """Return (in_scope_json, exclusions_json, seed_json)."""
    if job.project_id is None:
        return "[]", "[]", "[]"
    roe = getattr(job.project, "roe", None)
    scope = coerce_targets(getattr(roe, "in_scope", None) if roe else None)
    excl = coerce_targets(getattr(roe, "exclusions", None) if roe else None)
    seed = coerce_targets(getattr(roe, "seed", None) if roe else None)
    return json.dumps(scope), json.dumps(excl), json.dumps(seed)


def finish_job(job: Job, *, status: str, error: str = "", result: str | None = None) -> Job:
    job.status = status
    job.error = error
    if result is not None:
        job.result = result
    job.completed_at = dj_tz.now()
    job.save(
        update_fields=["status", "error", "result", "completed_at", "updated_at"]
    )

    sched = ObjectiveScheduler()
    recovery_replan = False
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
                recovery_replan = _enqueue_failure_replan(job, error)
            elif status == JobStatus.CANCELLED:
                if obj.status in {
                    ObjectiveStatus.PENDING,
                    ObjectiveStatus.IN_PROGRESS,
                    ObjectiveStatus.BLOCKED,
                }:
                    sched.mark(obj, ObjectiveStatus.CANCELLED)
    elif job.project_id and status == JobStatus.CANCELLED:
        ProjectLifecycle.cancel_open_objectives_for_roles(job.project, job.role_ids or [])

    if error:
        emit(job, "error", error)
    emit(job, "status", f"Job → {status}", job_status=status)

    if job.project_id and status == JobStatus.COMPLETED:
        try:
            nxt = ObjectiveScheduler().enqueue_next(job.project)
            if nxt is not None:
                emit(job, "log", f"Queued next objective job {nxt.id}")
        except Exception as exc:
            emit(job, "error", f"Failed to queue next objective: {exc}")

    if job.project_id:
        ProjectLifecycle.reconcile_project_status(job.project)
        if recovery_replan:
            # Keep the engagement live for the queued PM recovery job.
            project = job.project
            project.refresh_from_db()
            if project.status not in {
                ProjectStatus.CANCELLED,
                ProjectStatus.PAUSED,
            }:
                if project.status != ProjectStatus.ACTIVE:
                    project.status = ProjectStatus.ACTIVE
                    project.save(update_fields=["status", "updated_at"])
                set_crew_status(project, "running")

    if status in TERMINAL_JOB_STATUSES and job.children.exists():
        ProjectLifecycle.cascade_steer_children(
            job, "kill", note=f"Orphaned: parent job → {status}"
        )
    return job


def _enqueue_failure_replan(job: Job, error: str) -> bool:
    """Queue a project-manager REPLAN when a specialist objective job fails."""
    if not job.project_id or not job.objective_id:
        return False
    manager_id, _ = _bookend_ids()
    roles = [str(r).strip() for r in (job.role_ids or []) if str(r).strip()]
    if manager_id and roles == [manager_id]:
        return False
    from peon.projects.crew_control import replan_project
    from peon.projects.runtime_settings import PeonSettings

    max_r = max(0, int(PeonSettings.get_int("AGENT_MAX_FAILURE_REPLANS", 2) or 0))
    fails = Job.objects.filter(
        project_id=job.project_id,
        status=JobStatus.FAILED,
        objective_id__isnull=False,
    ).count()
    if fails > max_r:
        emit(
            job,
            "log",
            f"failure replan skipped (FAILED objective jobs={fails} > max={max_r})",
        )
        return False
    obj = job.objective
    seq = getattr(obj, "seq", "?")
    msg = (
        f"Specialist job failed for OBJ-{seq} "
        f"({', '.join(roles) or 'unknown role'}): {(error or 'failed')[:500]}\n"
        "Replan: unblock or replace the failed work; keep completed objectives; "
        "ensure the analyzer can still run when enough evidence exists."
    )
    try:
        result = replan_project(job.project, msg)
        emit(
            job,
            "log",
            f"Queued PM replan after failure: {result.get('primary_job_id') or '-'}",
        )
        return True
    except Exception as exc:
        emit(job, "error", f"failure replan enqueue failed: {exc}")
        return False

def _steer_stop(job: Job, lines: list[str]) -> Job | None:
    """If operator cancelled/paused mid-run, persist and return the job; else None."""
    job.refresh_from_db()
    joined = "\n\n".join(lines)
    if job.status == JobStatus.CANCELLED:
        return finish_job(
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
        "ROLES_DIR": str(settings.ROLES_DIR),
        "HELPERS_DIR": str(settings.HELPERS_DIR),
        "SANDBOX_ROLES_PATH": str(
            getattr(settings, "SANDBOX_ROLES_PATH", "") or settings.ROLES_DIR
        ),
        "TOOLS_CATALOG_DIR": str(settings.TOOLS_CATALOG_DIR),
        "ORCHESTRATOR_IN_SCOPE": scope_json,
        "ORCHESTRATOR_EXCLUSIONS": excl_json,
        "ORCHESTRATOR_SEED": seed_json,
    }
    if job.project_id:
        env["ORCHESTRATOR_PROJECT_ID"] = str(job.project_id)
    brief = (job.description or job.title or "").strip()
    if brief:
        env["ORCHESTRATOR_JOB_BRIEF"] = brief
    role_cmd = _operator_role_command(job)
    if role_cmd:
        env["ORCHESTRATOR_ROLE_COMMAND"] = role_cmd
    return JobEnv.bind(env)


def _operator_role_command(job: Job) -> str:
    """CLI for BinaryRunner: prefer objective scan/CLI over assert_* helpers."""
    candidates: list[str] = []
    obj = getattr(job, "objective", None)
    if obj is not None:
        candidates.extend(str(raw or "").strip() for raw in (obj.commands or []))
    brief = (job.description or "").strip()
    if brief and "\n" not in brief:
        candidates.append(brief)
    return pick_agent_command(*candidates)[:8000]


def _manager_needs_llm(job: Job) -> bool:
    """True when the manager bookend must run as an LLM (replan / steer)."""
    return JobDirective.objects.filter(
        job_id=job.id, consumed_at__isnull=True
    ).exists()


def _run_manager_dispatch(job: Job, *, role_id: str = "") -> Job:
    """Deterministic manager bookend: complete and let the scheduler enqueue next.

    Initial OBJ-1 dispatch is already owned by ``ObjectiveScheduler.enqueue_next``.
    Running CrewAI here only adds multi-minute reasoning for no new decisions.
    Replan / operator steer still use the LLM path.
    """
    stopped = _steer_stop(job, [])
    if stopped is not None:
        return stopped
    rid = (role_id or "").strip() or _bookend_ids()[0] or "project-manager"
    note = "Manager bookend: dispatch next ready objective (deterministic, no LLM)"
    emit(job, "status", note, role_id=rid)
    emit(job, "result", note, role_id=rid)
    return finish_job(
        job,
        status=JobStatus.COMPLETED,
        error="",
        result=f"## {rid}\nok=True\n{note}",
    )


def _job_needs_sandbox(role_ids: list[str]) -> bool:
    """True when any role needs Docker (CLI / sandbox tools). Report-only skips it."""
    sandbox_tools = frozenset(
        {
            "run_cli",
            "provision_cli",
            "run_periodic",
            "sandbox_setup",
            "sandbox_status",
        }
    )
    reg = RoleRegistry.shared()
    for rid in role_ids:
        role = reg.get(rid)
        if role is None:
            return True
        if role.allow_binaries:
            return True
        if sandbox_tools.intersection(role.tools or ()):
            return True
    return False


def run_job(job: Job) -> Job:
    """Provision sandbox, then run the Job agent (single executor)."""
    block = _roe_blocks(job)
    if block:
        return finish_job(job, status=JobStatus.FAILED, error=block, result="")

    names = [str(n).strip() for n in (job.role_ids or []) if str(n).strip()]
    if not names and job.objective_id and job.objective:
        names = ObjectiveScheduler().roles_for(job.objective)
        job.role_ids = names
        job.save(update_fields=["role_ids", "updated_at"])
    if not names:
        return finish_job(job, status=JobStatus.FAILED, error="No role_ids on Job")

    if job.objective_id and job.objective is not None:
        obj = job.objective
        if obj.status == ObjectiveStatus.CANCELLED:
            return finish_job(
                job,
                status=JobStatus.CANCELLED,
                error="Objective cancelled",
                result="",
            )
        if not ObjectiveScheduler().dependencies_met(obj):
            why = f"OBJ-{obj.seq} dependencies not met"
            ObjectiveScheduler().mark(obj, ObjectiveStatus.BLOCKED, reason=why)
            return finish_job(job, status=JobStatus.FAILED, error=why, result="")
        ObjectiveScheduler().mark(obj, ObjectiveStatus.IN_PROGRESS)

    emit(
        job,
        "status",
        f"Running roles: {', '.join(names)}",
        job_status=JobStatus.RUNNING,
    )

    manager_id, _analyzer_id = _bookend_ids()
    resume = bool(getattr(job, "resume_from_checkpoint", False))
    if resume:
        job.resume_from_checkpoint = False
        job.save(update_fields=["resume_from_checkpoint", "updated_at"])

    # Initial manager bookend: scheduler already owns "dispatch next specialist".
    # Skip sandbox + CrewAI unless replan/steer requires an LLM manager.
    # Analyzer bookend always runs as the AI role (reads raw artifacts + writes report).
    if (
        manager_id
        and names == [manager_id]
        and job.objective_id
        and not resume
        and not _manager_needs_llm(job)
    ):
        return _run_manager_dispatch(job, role_id=manager_id)

    ws = resolve_job_workspace(job)
    token = _bind_job_env(job, ws)
    try:
        if _job_needs_sandbox(names):
            try:
                project_id = str(job.project_id or "")
                sb = ProjectSandbox.provision(
                    project_id,
                    workspace=ws,
                    roles_dir=Path(settings.ROLES_DIR),
                    helpers_dir=Path(settings.HELPERS_DIR),
                    tools_dir=Path(settings.TOOLS_CATALOG_DIR),
                )
                emit(
                    job,
                    "log",
                    f"sandbox {sb.mode}:{sb.name} ({sb.action})"
                    + (
                        f" base_cmds={len(sb.base_commands)}"
                        if sb.base_commands
                        else ""
                    ),
                )
                cli_names: list[str] = []
                reg = RoleRegistry.shared()
                for rid in names:
                    role = reg.get(rid)
                    if role is None:
                        continue
                    for cli in role.allow_binaries:
                        if cli not in cli_names:
                            cli_names.append(cli)
                if cli_names:
                    provision = CatalogProvisioner.shared().provision(
                        cli_names, workspace=ws
                    )
                    if provision.installed:
                        emit(
                            job,
                            "log",
                            "provisioned: " + ", ".join(provision.installed),
                        )
                    if provision.verified:
                        emit(
                            job,
                            "log",
                            "verified CLIs: " + ", ".join(provision.verified),
                        )
                    if provision.errors:
                        emit(
                            job,
                            "error",
                            "CLI provision: " + "; ".join(provision.errors),
                        )
            except Exception as exc:
                emit(job, "error", f"sandbox/provision error: {exc}")
        else:
            emit(job, "log", "sandbox skipped (report/bridge-only role tools)")

        stopped = _steer_stop(job, [])
        if stopped is not None:
            return stopped

        return run_job_via_agent(
            job, role_ids=names, workspace=str(ws), resume=resume
        )
    finally:
        JobEnv.reset(token)
        Session.reset()


def run_job_via_agent(
    job: Job,
    *,
    role_ids: list[str],
    workspace: str,
    resume: bool = False,
) -> Job:
    """Execute one Job through orchestrator.agent.run (sandbox already bound)."""
    cfg = agent_run_config()
    depth = 1
    parent = job.parent
    while parent is not None:
        depth += 1
        parent = parent.parent

    extras: dict = {}
    names = list(role_ids)
    if names:
        extras["role_id"] = str(names[0])
    if job.project_id:
        project = job.project
        if getattr(project, "crew_flow_id", ""):
            extras["crew_flow_id"] = str(project.crew_flow_id)
        # Objective-backed projects use one Job per objective. Only a manager
        # Job outside that graph requests an all-in-one crew flow.
        manager_id, _ = _bookend_ids()
        if (
            manager_id
            and extras.get("role_id") == manager_id
            and not job.objective_id
        ):
            extras["crew_mode"] = "project"

    bridge = JobAgentBridge(job)
    steer_bits = bridge.drain_operator_guidance()
    steer = "\n\n".join(steer_bits).strip()
    if bridge.replan_requested:
        extras["replan"] = True

    scope = JobScope(
        job_id=str(job.id),
        project_id=str(job.project_id or ""),
        parent_job_id=str(job.parent_id or ""),
        workspace=workspace,
        role_ids=names,
        brief=(job.description or job.title or "").strip(),
        depth=depth,
        bridge=bridge,
        extras=extras,
    )
    emit(
        job,
        "status",
        f"Agent runtime (max_iter={cfg.max_iterations}"
        + ("; resume" if resume else "")
        + (f"; role={extras.get('role_id')}" if extras.get("role_id") else "")
        + ("; replan" if extras.get("replan") else "")
        + ")",
    )
    if job.project_id and extras.get("crew_mode") == "project":
        try:
            set_crew_status(job.project, "running")
        except Exception:
            pass
    result = run_agent(scope, cfg, resume=resume, steer=steer)
    if job.project_id and extras.get("crew_mode") == "project":
        try:
            set_crew_status(
                job.project,
                "done" if result.ok else "stopped",
                flow_id=str(scope.extras.get("crew_flow_id") or ""),
            )
        except Exception:
            pass
    try:
        n = ingest_workspace_findings(job, Path(workspace))
        if n:
            emit(job, "log", f"ingested {n} finding(s) from queue")
    except Exception as exc:
        emit(job, "error", f"finding ingest: {exc}")

    if result.ok:
        return finish_job(
            job,
            status=JobStatus.COMPLETED,
            error="",
            result=result.output or "",
        )
    return finish_job(
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
        return finish_job(job, status=JobStatus.FAILED, error=str(exc))
