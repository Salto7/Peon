"""Per-role CrewAI job runtime (manager role expands to project crew)."""

from __future__ import annotations

from orchestrator.agent.job import bind_job
from orchestrator.agent.runtime_base import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeBase,
)
from orchestrator.crew.roles.model import build_crew_agent
from orchestrator.crew.roles.registry import RoleRegistry
from orchestrator.crew.runtimes.project_crewai import run_project_crew_from_scope
from orchestrator.utils.stream_events import envelope


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


def project_crew_requested(scope) -> bool:
    """True only for explicit project-crew runs, never merely for a manager role."""
    extras = getattr(scope, "extras", None) or {}
    return str(extras.get("crew_mode") or "").strip().lower() == "project"


class CrewAIJobRuntime(AgentRuntimeBase):
    """Run one CrewAI role, or an explicitly requested full project crew."""

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

        if project_crew_requested(scope):
            planned = tuple(
                str(r).strip()
                for r in (scope.role_ids or ())
                if str(r).strip() and str(r).strip() != role_id
            )
            extra_roles = scope.extras.get("crew_role_ids") or scope.extras.get(
                "specialist_role_ids"
            )
            if extra_roles:
                planned = tuple(
                    str(r).strip() for r in extra_roles if str(r or "").strip()
                ) or planned
            crew_result = run_project_crew_from_scope(
                scope,
                request.config,
                role_ids=planned,
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

        brief = (request.steer or scope.brief or role.goal).strip()
        if request.resume and not request.steer:
            brief = (
                "Continue from your last work under Rules of Engagement. "
                "Do not repeat completed work.\n\n" + brief
            ).strip()

        scope.extras = dict(scope.extras or {})
        scope.extras["role_id"] = role.id

        try:
            agent = build_crew_agent(role)
        except Exception as exc:
            scope.bridge.emit("error", f"crew agent build failed: {exc}")
            return AgentRunResult(ok=False, error=str(exc))

        scope.bridge.emit(
            "status",
            f"crewai role={role.id} starting",
            metadata=envelope("crewai_role_start", {"role_id": role.id}),
        )

        with bind_job(scope, request.config):
            try:
                result = agent.kickoff(brief[:8000])
            except Exception as exc:
                scope.bridge.emit("error", f"crewai role failed: {exc}")
                return AgentRunResult(ok=False, error=str(exc))

        raw = getattr(result, "raw", None)
        output = str(raw if raw is not None else result or "")
        scope.bridge.emit(
            "status",
            f"crewai role={role.id} finished",
            metadata=envelope("crewai_role_done", {"role_id": role.id}),
        )
        return AgentRunResult(ok=True, output=output, iterations=1)
