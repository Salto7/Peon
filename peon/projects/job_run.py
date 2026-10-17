"""Job execution and agent-command helpers."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from django.conf import settings
from django.utils import timezone as dj_tz

from agent_runtime.api import Session
from orchestrator.agent import JobScope, run_agent
from orchestrator.crew.roles.registry import RoleRegistry
from orchestrator.tools.catalog import CatalogProvisioner
from orchestrator.utils.job_env import JobEnv
from peon.projects.agent_bridge import JobAgentBridge, agent_run_config
from peon.projects.crew_control import replan_project, set_crew_status
from peon.projects.findings import ingest_workspace_findings
from peon.projects.job_claim import claim_next_job, emit
from peon.projects.lifecycle import cascade_steer_children, reconcile_project_status
from peon.projects.models import (
    INACTIVE_PROJECT_STATUSES,
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

logger = logging.getLogger(__name__)


def _roe_blocks(job: Job) -> str | None:
    """Fail-closed for active probe roles with empty in_scope (type ignored)."""
    if job.project_id is None:
        return None
    project = job.project
    roe, _ = provision_project_roe(
        project,
        texts=[project.summary or "", project.title or ""],
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
    from peon.projects.db import refresh_project_kpis, using_project

    job.status = status
    job.error = error
    if result is not None:
        job.result = result
    job.completed_at = dj_tz.now()
    with using_project(job.project_id):
        job.save(update_fields=["status", "error", "result", "completed_at", "updated_at"])
    if job.project_id:
        try:
            refresh_project_kpis(job.project_id)
        except Exception as exc:
            logger.warning("refresh_project_kpis failed: %s", exc, exc_info=True)
    # Mandatory domain checkpoint before any downstream scheduling.
    try:
        from peon.projects.checkpoints import write_job_checkpoint

        write_job_checkpoint(job, reason="job_finished")
    except Exception:
        from peon.projects.checkpoints import require_checkpoint_before_terminal

        require_checkpoint_before_terminal(job)

    sched = ObjectiveScheduler()
    recovery_replan = False
    if job.project_id and job.objective_id:
        obj = job.objective
        if obj is not None:
            if (
                status == JobStatus.FAILED
                and job.parent_id is None
                and obj.status in {ObjectiveStatus.PENDING, ObjectiveStatus.IN_PROGRESS}
            ):
                sched.mark(
                    obj,
                    ObjectiveStatus.BLOCKED,
                    reason=(error or "Job failed")[:2000],
                )
                recovery_replan = _enqueue_failure_replan(job, error)
            sched.settle(obj)

    # Structured transcript for finish.
    try:
        from orchestrator.agent.transcript import record_from_event
        from orchestrator.utils.stream_events import EVENT_WORK_FINISHED

        bridge = JobAgentBridge(job)
        bridge.append_transcript(
            record_from_event(
                category="error" if status == JobStatus.FAILED else "outcome",
                summary=f"job → {status}" + (f": {(error or '')[:400]}" if error else ""),
                project_id=str(job.project_id or ""),
                objective_id=str(job.objective_id or ""),
                job_id=str(job.id),
                role_id=str((job.role_ids or [""])[0] if job.role_ids else ""),
                event=EVENT_WORK_FINISHED,
                payload={
                    "status": status,
                    "parent_job_id": str(job.parent_id or ""),
                },
            )
        )
    except Exception as exc:
        logger.warning("work-finished transcript failed: %s", exc, exc_info=True)

    if error:
        emit(job, "error", error)
    emit(job, "status", f"Job → {status}", job_status=status)

    # Report up to the spawning parent (wakes it only if parked waiting).
    if job.parent_id and job.project_id:
        try:
            from peon.projects import a2a

            a2a.deliver_parent_handoff(job)
        except Exception as exc:
            logger.warning("parent handoff failed for %s: %s", job.id, exc, exc_info=True)

    if job.project_id and status == JobStatus.COMPLETED:
        try:
            project = job.project
            queued = ObjectiveScheduler().enqueue_ready(project)
            if queued:
                ids = ", ".join(str(j.id) for j in queued[:8])
                emit(
                    job,
                    "log",
                    f"Queued {len(queued)} ready objective job(s): {ids}",
                )
        except Exception as exc:
            emit(job, "error", f"Failed to queue ready objectives: {exc}")
        try:
            from peon.projects.operator_spawn import maybe_reopen_analyzer_after_child

            ana_job = maybe_reopen_analyzer_after_child(job)
            if ana_job is not None:
                emit(
                    job,
                    "log",
                    f"Re-queued analyzer after child artifacts: {ana_job.id}",
                )
        except Exception as exc:
            emit(job, "error", f"Analyzer re-queue after child failed: {exc}")

    if job.project_id:
        reconcile_project_status(job.project)
        if recovery_replan:
            # Keep the engagement live for the queued PM recovery job.
            project = job.project
            project.refresh_from_db()
            if project.status not in INACTIVE_PROJECT_STATUSES:
                if project.status != ProjectStatus.ACTIVE:
                    project.status = ProjectStatus.ACTIVE
                    project.save(update_fields=["status", "updated_at"])
                set_crew_status(project, "running")

    # Spawn fan-out: COMPLETED parents leave children running (claim allows that).
    # FAILED / CANCELLED still cascade-kill so the tree cannot orphan forever.
    if status in {JobStatus.FAILED, JobStatus.CANCELLED} and job.children.exists():
        cascade_steer_children(job, "kill", note=f"Orphaned: parent job → {status}")
    return job


def _enqueue_failure_replan(job: Job, error: str) -> bool:
    """Queue a project-manager REPLAN when a specialist objective job fails.

    When the failure-replan budget is exhausted, raise a typed HITL prompt
    instead of silently stalling.
    """
    if not job.project_id or not job.objective_id:
        return False
    manager_id, _ = RoleRegistry.shared().engagement_bookends()
    roles = [str(r).strip() for r in (job.role_ids or []) if str(r).strip()]
    if manager_id and roles == [manager_id]:
        return False
    from peon.projects.hitl import failure_exhausted_prompt
    from peon.projects.runtime_settings import PeonSettings

    max_r = max(0, int(PeonSettings.get_int("AGENT_MAX_FAILURE_REPLANS", 2) or 0))
    from peon.projects.db import qs_using

    fails = (
        qs_using(Job, job.project_id)
        .filter(
            project_id=job.project_id,
            status=JobStatus.FAILED,
            objective_id__isnull=False,
        )
        .count()
    )
    if fails > max_r:
        emit(
            job,
            "log",
            f"failure replan skipped (FAILED objective jobs={fails} > max={max_r}) — HITL",
        )
        try:
            failure_exhausted_prompt(job, error, fails=fails, max_r=max_r)
        except Exception as exc:
            emit(job, "error", f"failure HITL prompt failed: {exc}")
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
        try:
            failure_exhausted_prompt(job, error, fails=fails, max_r=max_r)
        except Exception as exc:
            logger.warning("failure_exhausted_prompt failed: %s", exc, exc_info=True)
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
        "ORCHESTRATOR_STREAM_SOCKET": str(getattr(settings, "STREAM_SOCKET_PATH", "") or ""),
        "SKILLS_DIR": str(settings.SKILLS_DIR),
        "TOOLS_CATALOG_DIR": str(settings.TOOLS_CATALOG_DIR),
        "ORCHESTRATOR_IN_SCOPE": scope_json,
        "ORCHESTRATOR_EXCLUSIONS": excl_json,
        "ORCHESTRATOR_SEED": seed_json,
    }
    if job.project_id:
        env["ORCHESTRATOR_PROJECT_ID"] = str(job.project_id)
    if job.role_ids:
        env["ORCHESTRATOR_ROLE_ID"] = str(job.role_ids[0])
    brief = (job.description or job.title or "").strip()
    if brief:
        env["ORCHESTRATOR_JOB_BRIEF"] = brief
    return JobEnv.bind(env)


def _manager_needs_llm(job: Job) -> bool:
    """True when the manager bookend must run as an LLM (replan / steer)."""
    from peon.projects.db import project_alias_or_default
    from peon.projects.models import AgentMessage

    alias = project_alias_or_default(job.project_id)
    return (
        JobDirective.objects.using(alias).filter(job_id=job.id, consumed_at__isnull=True).exists()
        or AgentMessage.objects.using(alias)
        .filter(to_job_id=job.id, delivered_at__isnull=True)
        .exists()
    )


def _run_manager_dispatch(job: Job, *, role_id: str = "") -> Job:
    """Deterministic manager bookend: complete and let the scheduler enqueue next.

    Initial OBJ-1 dispatch is already owned by ``ObjectiveScheduler.enqueue_next``.
    Running CrewAI here only adds multi-minute reasoning for no new decisions.
    Replan / operator steer still use the LLM path.
    """
    stopped = _steer_stop(job, [])
    if stopped is not None:
        return stopped
    rid = (role_id or "").strip() or RoleRegistry.shared().engagement_bookends()[0] or "manager"
    note = "Manager bookend: dispatch next ready objective (deterministic, no LLM)"
    emit(job, "status", note, role_id=rid)
    emit(job, "result", note, role_id=rid)
    return finish_job(
        job,
        status=JobStatus.COMPLETED,
        error="",
        result=f"## {rid}\nok=True\n{note}",
    )


def _job_skill_ids(job: Job) -> list[str]:
    skills = [str(s).strip() for s in (getattr(job, "skill_ids", None) or []) if str(s).strip()]
    if not skills and job.objective_id and job.objective is not None:
        skills = [
            str(s).strip()
            for s in (getattr(job.objective, "skill_ids", None) or [])
            if str(s).strip()
        ]
    return skills


# Crew / capability names that execute inside the bound project sandbox.
_SANDBOX_COMMAND_TOOLS = frozenset(
    {
        "bash",
        "run_skill_script",
        "run_periodic",
        "sandbox_status",
        "provision_cli",
        "load_skill",
        "read_skill_resource",
    }
)


def _job_needs_sandbox(role_ids: list[str], skill_ids: list[str] | None = None) -> bool:
    """True when this job will run commands. Those run only in its sandbox.

    Technical and manager agents receive Claude ``bash``. Skill packs may add
    ``run_skill_script`` / progressive tools. Any of those require the worker
    to bind the project sandbox before the agent starts.
    """
    from orchestrator.skills.bind import (
        effective_tools,
        progressive_tool_names,
        resolve_skill_ids,
    )

    requested = list(skill_ids or [])
    reg = RoleRegistry.shared()
    for rid in role_ids:
        role = reg.get(rid)
        if role is None:
            return True
        if role.is_technical or role.is_manager:
            return True
        if role.allow_binaries:
            return True
        selected = resolve_skill_ids(role, requested or None)
        if progressive_tool_names(selected, role_id=rid):
            return True
        surface = set(role.tools or ()) | set(effective_tools(role, selected))
        if _SANDBOX_COMMAND_TOOLS.intersection(surface):
            return True
    return False


def _defer_for_objective_dependencies(job: Job) -> Job:
    """Park a claimed job as IDLE until its objective dependencies complete.

    No re-queue and no polling: ``ObjectiveScheduler.enqueue_ready`` delivers
    the dependency handoffs and wakes the job when the last one finishes.
    """
    from peon.projects import a2a

    parked = a2a.park_idle(job, reason="dependencies")
    # A dependency may have completed between the check and parking; that
    # completion's wake would have found the job RUNNING and skipped it.
    parked.refresh_from_db()
    if parked.status == JobStatus.IDLE and a2a.can_wake(parked):
        a2a.deliver_handoffs(parked.objective)
        a2a.wake_job(parked, reason="dependencies completed while parking")
        parked.refresh_from_db()
    return parked


def run_job(job: Job) -> Job:
    """Provision sandbox, then run the Job agent (single executor)."""
    # Skills edited on disk since the last run must reach this job's sandbox.
    try:
        from peon.projects.platform_catalog import PlatformCatalog

        PlatformCatalog.shared().refresh_stale_skills()
    except Exception as exc:  # noqa: BLE001
        logger.warning("skill refresh failed: %s", exc, exc_info=True)
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
        if obj.status == ObjectiveStatus.COMPLETED:
            return finish_job(
                job,
                status=JobStatus.CANCELLED,
                error="Objective already completed",
                result="",
            )
        if not ObjectiveScheduler().dependencies_met(obj):
            return _defer_for_objective_dependencies(job)
        if obj.status != ObjectiveStatus.IN_PROGRESS:
            ObjectiveScheduler().mark(obj, ObjectiveStatus.IN_PROGRESS)

    block = _roe_blocks(job)
    if block:
        return finish_job(job, status=JobStatus.FAILED, error=block, result="")

    emit(
        job,
        "status",
        f"Running roles: {', '.join(names)}",
        job_status=JobStatus.RUNNING,
    )

    resume = bool(getattr(job, "resume_from_checkpoint", False))
    if resume:
        job.resume_from_checkpoint = False
        job.save(update_fields=["resume_from_checkpoint", "updated_at"])

    # Initial manager bookend: scheduler already owns "dispatch next specialist".
    # Skip sandbox + CrewAI unless replan/steer requires an LLM manager.
    # Analyzer bookend runs as the AI role (reads artifacts + writes report).
    manager_id, _analyzer_id = RoleRegistry.shared().engagement_bookends()
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
        if _job_needs_sandbox(names, _job_skill_ids(job)):
            try:
                project_id = str(job.project_id or "")
                sb = ProjectSandbox.provision(project_id)
                emit(
                    job,
                    "log",
                    f"sandbox {sb.mode}:{sb.name} ({sb.action})"
                    + (f" base_cmds={len(sb.base_commands)}" if sb.base_commands else ""),
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
                    provision = CatalogProvisioner.shared().provision(cli_names, workspace=ws)
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

        return run_job_via_agent(job, role_ids=names, workspace=str(ws), resume=resume)
    finally:
        JobEnv.reset(token)
        Session.reset()


def _park_after_run(job: Job, bridge: JobAgentBridge, output: str) -> Job | None:
    """The agent asked to go idle: keep its partial output, park, and re-check mail.

    Returns the parked job, or ``None`` when nothing can ever wake it (the
    caller then finishes it normally).
    """
    from peon.projects import a2a
    from peon.projects.db import using_project

    if not bridge.other_agents_alive():
        emit(
            job,
            "log",
            "go_idle ignored: no other agent is alive to message this one — finishing.",
        )
        return None
    from peon.projects.db import project_alias_or_default

    alias = project_alias_or_default(job.project_id)
    with using_project(job.project_id):
        Job.objects.using(alias).filter(pk=job.pk).update(result=output[:200_000])
    job.refresh_from_db()
    a2a.park_idle(job, reason="message", note=bridge.idle_note, checkpoint=True)
    # A message may have arrived while the agent was finishing its turn.
    job.refresh_from_db()
    if job.status == JobStatus.IDLE and a2a.unread(job) and a2a.can_wake(job):
        a2a.wake_job(job, reason="message arrived while going idle")
        job.refresh_from_db()
    return job


def _is_dispatch_supervisor(job: Job, role_ids: list[str]) -> bool:
    """True when this job must delegate execution to spawned workers.

    A specialist (technical role) job that is not itself a spawned worker owns
    the lane: it plans, spawns skill-bound workers and verifies. Spawned
    children, the PM/analyzer bookends and the resident governor keep their
    normal tools.
    """
    from peon.projects.a2a import GOVERNANCE_PROFILE
    from peon.projects.runtime_settings import PeonSettings

    if job.parent_id or job.profile == GOVERNANCE_PROFILE:
        return False
    if not PeonSettings.get_bool("AGENT_DISPATCH_ONLY", True):
        return False
    if PeonSettings.get_int("AGENT_MAX_SUBAGENTS", 8) <= 0:
        return False  # nothing could be spawned
    role = RoleRegistry.shared().get(role_ids[0]) if role_ids else None
    return bool(
        role is not None and role.is_technical and not role.is_manager and not role.is_analyzer
    )


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
    skills = _job_skill_ids(job)
    if skills:
        extras["skill_ids"] = skills
    if job.project_id:
        project = job.project
        if getattr(project, "crew_flow_id", ""):
            extras["crew_flow_id"] = str(project.crew_flow_id)
        # Objective-backed projects use one Job per objective. Only a manager
        # Job outside that graph requests an all-in-one crew flow.
        from peon.projects.a2a import GOVERNANCE_PROFILE

        manager_id, _ = RoleRegistry.shared().engagement_bookends()
        if (
            manager_id
            and extras.get("role_id") == manager_id
            and not job.objective_id
            and job.profile != GOVERNANCE_PROFILE  # resident governor runs solo
        ):
            extras["crew_mode"] = "project"

    extras["dispatch_only"] = _is_dispatch_supervisor(job, names)

    bridge = JobAgentBridge(job)
    steer_bits = bridge.drain_operator_guidance()
    try:
        # Memory index first: every agent starts knowing what is already known.
        if job.project_id:
            from peon.projects import memory_store

            manager_id, _analyzer = RoleRegistry.shared().engagement_bookends()
            rid = (names[0] if names else "") or ""
            block = memory_store.index_block(
                job.project, role_id=rid, manager=bool(manager_id and rid == manager_id)
            )
            if block:
                steer_bits.insert(0, block)
    except Exception as exc:
        logger.warning("memory index injection failed: %s", exc, exc_info=True)
    try:
        from peon.projects import a2a

        inbox = a2a.render_messages(a2a.drain_inbox(job))
        if inbox:
            steer_bits.append(inbox)
    except Exception as exc:
        logger.warning("a2a inbox drain failed: %s", exc, exc_info=True)
    if resume:
        try:
            from peon.projects.checkpoints import resume_context_text

            ckpt_text = resume_context_text(job)
            if ckpt_text:
                steer_bits.insert(0, ckpt_text)
        except Exception as exc:
            logger.warning("resume_context_text failed: %s", exc, exc_info=True)
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
        except Exception as exc:
            logger.warning("set_crew_status failed: %s", exc, exc_info=True)
    result = run_agent(scope, cfg, resume=resume, steer=steer)
    # The resident governance PM re-parks after each turn instead of completing,
    # so it stays available to the crew for the whole engagement.
    from peon.projects import a2a
    from peon.projects.a2a import GOVERNANCE_PROFILE

    if result.ok and job.profile == GOVERNANCE_PROFILE:
        from peon.projects.db import project_alias_or_default, using_project

        alias = project_alias_or_default(job.project_id)
        with using_project(job.project_id):
            Job.objects.using(alias).filter(pk=job.pk).update(
                result=(result.output or "")[:200_000]
            )
        job.refresh_from_db()
        return a2a.repark_governor(job)
    if result.ok and bridge.idle_requested:
        parked = _park_after_run(job, bridge, result.output or "")
        if parked is not None:
            return parked
    if job.project_id and extras.get("crew_mode") == "project":
        try:
            set_crew_status(
                job.project,
                "done" if result.ok else "stopped",
                flow_id=str(scope.extras.get("crew_flow_id") or ""),
            )
        except Exception as exc:
            logger.warning("set_crew_status failed: %s", exc, exc_info=True)
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
