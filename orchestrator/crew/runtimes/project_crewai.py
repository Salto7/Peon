"""Project-level CrewAI runtime (hierarchical manager crew).

One implementation, one caller (``job_crewai.py``) — a plain function, not an
ABC/DTO pair, since nothing else needs the extra indirection.
"""

from __future__ import annotations

import uuid
from typing import Any

from orchestrator.agent.config import AgentRunConfig
from orchestrator.agent.job import JobScope, bind_job
from orchestrator.agent.runtime_base import AgentRunResult
from orchestrator.crew.flows.engagement import build_engagement_crew
from orchestrator.crew.roles.registry import RoleRegistry
from orchestrator.utils import llm_usage
from orchestrator.utils.stream_events import (
    EVENT_PROJECT_CREW_DONE,
    EVENT_PROJECT_CREW_START,
    envelope,
)


def run_project_crew(
    scope: JobScope,
    config: AgentRunConfig | None = None,
    *,
    role_ids: tuple[str, ...] = (),
    resume: bool = False,
    replan: bool = False,
    steer: str = "",
    flow_id: str = "",
) -> AgentRunResult:
    """Run manager → specialists → analyzer for one project.

    Used by the job runtime when the manager role owns the project.
    """
    cfg = config or AgentRunConfig()
    if not cfg.runtime_enabled:
        return AgentRunResult(
            ok=False, error="agent runtime disabled (AGENT_RUNTIME_ENABLED=false)"
        )

    flow_id = (flow_id or "").strip() or str(uuid.uuid4())
    brief = (scope.brief or "").strip()
    if steer:
        brief = f"{brief}\n\nOPERATOR INSTRUCTION:\n{steer.strip()}".strip()

    scope.extras = dict(scope.extras or {})
    mgr = RoleRegistry.shared().manager()
    if mgr:
        scope.extras["role_id"] = mgr.id
    scope.extras["crew_flow_id"] = flow_id
    scope.extras["crew_mode"] = "project"

    try:
        crew = build_engagement_crew(
            brief=brief,
            role_ids=role_ids,
            replan_note=steer if replan else "",
            config=cfg,
        )
    except Exception as exc:
        scope.bridge.emit("error", f"crew build failed: {exc}")
        return AgentRunResult(ok=False, error=str(exc))

    action = "replan" if replan else ("resume" if resume else "start")
    scope.bridge.emit(
        "status",
        f"project crew {action} flow={flow_id[:8]}",
        metadata=envelope(
            EVENT_PROJECT_CREW_START,
            {"flow_id": flow_id, "action": action},
        ),
    )

    with bind_job(scope, cfg), llm_usage.purpose("agent_loop_crew"):
        try:
            result = crew.kickoff()
        except Exception as exc:
            scope.bridge.emit("error", f"project crew failed: {exc}")
            return AgentRunResult(ok=False, error=str(exc))

    output = _crew_output(result)
    scope.bridge.emit(
        "status",
        f"project crew done flow={flow_id[:8]}",
        metadata=envelope(EVENT_PROJECT_CREW_DONE, {"flow_id": flow_id}),
    )
    return AgentRunResult(ok=True, output=output, iterations=1)


def _crew_output(result: Any) -> str:
    if result is None:
        return ""
    raw = getattr(result, "raw", None)
    if raw is not None:
        return str(raw)
    return str(result)
