"""Per-role CrewAI job runtime (manager project-crew when the job asks for it)."""

from __future__ import annotations

from orchestrator.agent.backends import AgentBackend
from orchestrator.agent.backends.crewai.factory import build_crew_agent
from orchestrator.agent.backends.crewai.project import run_project_crew
from orchestrator.agent.job import bind_job
from orchestrator.agent.runtime_base import AgentRunRequest, AgentRunResult
from orchestrator.crew.roles.registry import RoleRegistry
from orchestrator.utils import llm_usage
from orchestrator.utils.stream_events import EVENT_ROLE_DONE, EVENT_ROLE_START, envelope


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


def _open(
    request: AgentRunRequest,
) -> tuple[AgentRunResult | None, object, object]:
    if not request.config.runtime_enabled:
        return (
            AgentRunResult(
                ok=False,
                error="agent runtime disabled (AGENT_RUNTIME_ENABLED=false)",
            ),
            None,
            None,
        )
    scope = request.scope
    role_id = resolve_role_id(scope)
    if not role_id:
        return (
            AgentRunResult(
                ok=False,
                error=(
                    "AGENT_MODULE=crewai requires a role id "
                    "(extras.role_id or role_ids matching the "
                    "platform role store)"
                ),
            ),
            None,
            None,
        )
    try:
        role = RoleRegistry.shared().require(role_id)
    except KeyError as exc:
        return AgentRunResult(ok=False, error=str(exc)), None, None
    return None, scope, role


class CrewAIBackend(AgentBackend):
    """Run one CrewAI role, or an explicitly requested full project crew."""

    supports_project_crew = True

    def warm(self) -> None:
        import crewai  # noqa: F401 — pay the import cost before the first job

    def run_project(self, request: AgentRunRequest) -> AgentRunResult:
        err, scope, _role = _open(request)
        if err is not None:
            return err
        # Drop the id the job asked for, alias included.
        role_id = resolve_role_id(scope)
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
        return run_project_crew(
            scope,
            request.config,
            role_ids=planned,
            resume=request.resume,
            replan=bool(scope.extras.get("replan")),
            steer=request.steer,
            flow_id=str(scope.extras.get("crew_flow_id") or ""),
        )

    def run_job(self, request: AgentRunRequest) -> AgentRunResult:
        err, scope, role = _open(request)
        if err is not None:
            return err

        # The objective brief always stays; steer (operator guidance, A2A
        # inbox, memory, checkpoint context) is added to it — never a
        # replacement, or a woken agent would lose its own task.
        base = (scope.brief or "").strip()
        steer = (request.steer or "").strip()
        brief = "\n\n".join(b for b in (base, steer) if b) or role.goal.strip()
        if request.resume and not steer:
            brief = (
                "Continue from your last work under Rules of Engagement. "
                "Do not repeat completed work.\n\n" + brief
            ).strip()

        scope.extras = dict(scope.extras or {})
        scope.extras["role_id"] = role.id
        from orchestrator.skills.bind import resolve_skill_ids

        skill_ids = resolve_skill_ids(role, scope.extras.get("skill_ids"))
        scope.extras["skill_ids"] = list(skill_ids)
        # RoE is enforced by the parent engagement / planner, not per skill job.

        try:
            agent = build_crew_agent(
                role,
                skill_ids=skill_ids,
                max_iterations=request.config.max_iterations,
                reasoning_effort=request.config.crew_reasoning_effort,
                reasoning_max_attempts=request.config.crew_reasoning_max_attempts,
                dispatch_only=bool(scope.extras.get("dispatch_only")),
            )
        except Exception as exc:
            scope.bridge.emit("error", f"crew agent build failed: {exc}")
            return AgentRunResult(ok=False, error=str(exc))

        scope.bridge.emit(
            "status",
            f"crewai role={role.id} skills={','.join(skill_ids) or '-'} starting",
            metadata=envelope(
                EVENT_ROLE_START,
                {"role_id": role.id, "skill_ids": list(skill_ids)},
            ),
        )

        loop_purpose = (
            "agent_loop_dispatch" if scope.extras.get("dispatch_only") else "agent_loop"
        )
        with bind_job(scope, request.config), llm_usage.purpose(loop_purpose):
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
            metadata=envelope(EVENT_ROLE_DONE, {"role_id": role.id}),
        )
        return AgentRunResult(ok=True, output=output, iterations=1)
