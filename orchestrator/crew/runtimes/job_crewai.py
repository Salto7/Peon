"""Per-role CrewAI job runtime (manager role expands to project crew)."""

from __future__ import annotations

from orchestrator.agent.job import bind_job
from orchestrator.agent.runtime_base import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeBase,
)
from orchestrator.crew.checkpoint import build_memory, checkpoint_config
from orchestrator.crew.roles.factory import (
    build_crew_agent,
    restore_agent_runtime_policy,
)
from orchestrator.crew.roles.registry import RoleRegistry
from orchestrator.crew.runtimes.project_crewai import run_project_crew_from_scope
from orchestrator.crew.runtime_support import drain_agent_inbox


def resolve_role_id(scope) -> str:
    """Prefer extras.role_id; else first role_ids entry that matches a role."""
    extras = getattr(scope, "extras", None) or {}
    rid = str(extras.get("role_id") or "").strip()
    if rid:
        return rid
    for name in scope.role_ids or []:
        candidate = str(name or "").strip()
        if candidate and RoleRegistry.shared().get(candidate):
            return candidate
    return ""


class CrewAIJobRuntime(AgentRuntimeBase):
    """Run one CrewAI role, or the full project crew when role is project-manager."""

    def start(self, request: AgentRunRequest) -> AgentRunResult:
        if not request.config.runtime_enabled:
            return AgentRunResult(
                ok=False,
                error="agent runtime disabled (AGENT_RUNTIME_ENABLED=false)",
            )
        scope = request.scope
        role_id = resolve_role_id(scope)
        if not role_id:
            return AgentRunResult(
                ok=False,
                error=(
                    "AGENT_MODULE=crewai requires a role id "
                    "(extras.role_id or role_ids matching roles/*)"
                ),
            )
        try:
            role = RoleRegistry.shared().require(role_id)
        except KeyError as exc:
            return AgentRunResult(ok=False, error=str(exc))

        if scope.extras.get("crew_mode") == "project":
            crew_result = run_project_crew_from_scope(
                scope,
                request.config,
                role_ids=tuple(scope.extras.get("project_role_ids") or ()),
                resume=request.resume,
                replan=bool(scope.extras.get("replan")),
                steer=request.steer,
                flow_id=str(scope.extras.get("crew_flow_id") or ""),
            )
            return AgentRunResult(
                ok=crew_result.ok,
                output=crew_result.output,
                error=crew_result.error,
                iterations=1,
            )

        if role.requires_roe:
            summary = scope.bridge.roe_summary()
            blocked = (
                "No Rules of Engagement" in summary
                or "in_scope: (empty)" in summary
                or "not configured" in summary.lower()
            )
            if blocked:
                return AgentRunResult(
                    ok=False,
                    error=f"role {role_id} requires non-empty RoE in_scope",
                )

        brief = (scope.brief or role.goal).strip()
        if request.steer:
            brief = (
                f"{brief}\n\nOPERATOR INSTRUCTION:\n{request.steer.strip()}"
            ).strip()
        inbox = drain_agent_inbox(scope)
        if inbox:
            brief = f"{brief}\n\nAGENT INBOX:\n{inbox}".strip()
        if request.resume:
            brief = (
                "Continue from the native CrewAI checkpoint under Rules of "
                "Engagement. Do not repeat completed work.\n\n" + brief
            ).strip()
            scope.bridge.emit(
                "status",
                "resuming CrewAI agent from durable checkpoint",
                metadata={"event": "agent_resume", "thread_id": str(scope.job_id)},
            )

        scope.extras = dict(scope.extras or {})
        scope.extras["role_id"] = role.id

        try:
            memory = build_memory(scope) if request.config.memory_enabled else None
            checkpoint = (
                checkpoint_config(scope, resume=False)
                if request.config.checkpoint_enabled
                else None
            )
            agent = build_crew_agent(
                role,
                max_iterations=request.config.max_iterations,
                max_replans=request.config.max_failure_replans,
                memory=memory,
                checkpoint=checkpoint,
                max_execution_time=request.config.max_execution_seconds or None,
            )
        except Exception as exc:
            scope.bridge.emit("error", f"crew agent build failed: {exc}")
            return AgentRunResult(ok=False, error=str(exc))

        scope.bridge.emit(
            "status",
            f"crewai role={role.id} starting",
            metadata={"event": "crewai_role_start", "role_id": role.id},
        )

        with bind_job(scope, request.config):
            try:
                restore = (
                    checkpoint_config(scope, resume=True)
                    if request.resume and request.config.checkpoint_enabled
                    else None
                )
                if restore is not None and restore.restore_from is not None:
                    from crewai import Agent

                    agent = Agent.from_checkpoint(restore)
                    restore_agent_runtime_policy(
                        agent,
                        max_iterations=request.config.max_iterations,
                        max_replans=request.config.max_failure_replans,
                        max_execution_time=(
                            request.config.max_execution_seconds or None
                        ),
                        memory=memory,
                        checkpoint=checkpoint,
                    )
                result = agent.kickoff(brief[:8000])
            except Exception as exc:
                scope.bridge.emit("error", f"crewai role failed: {exc}")
                return AgentRunResult(ok=False, error=str(exc))

        raw = getattr(result, "raw", None)
        output = str(raw if raw is not None else result or "")
        scope.bridge.emit(
            "status",
            f"crewai role={role.id} finished",
            metadata={"event": "crewai_role_done", "role_id": role.id},
        )
        replans = int(getattr(result, "replan_count", 0) or 0)
        return AgentRunResult(ok=True, output=output, iterations=max(1, replans + 1))
