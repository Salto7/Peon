"""Build CrewAI Agent instances from RoleSpec + allowlisted tools."""

from __future__ import annotations

from typing import Any

from orchestrator.config import get_config
from orchestrator.crew.roles.model import RoleSpec
from orchestrator.crew.tools import build_tools


def llm_id_for_crew() -> str:
    """Map Peon LiteLLM model id into a CrewAI-friendly llm string."""
    cfg = get_config()
    model = (cfg.litellm_model or "").strip()
    return model or "openrouter/openai/gpt-4o-mini"


def build_crew_agent(
    role: RoleSpec,
    *,
    tools: list[Any] | None = None,
    max_iterations: int | None = None,
    max_replans: int = 2,
    memory: Any | None = None,
    checkpoint: Any | None = None,
    max_execution_time: int | None = None,
) -> Any:
    """Instantiate a CrewAI ``Agent`` for ``role`` (lazy crewai import)."""
    try:
        from crewai import Agent
        from crewai.tools.tool_failure import ToolFailurePolicy
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "crewai is required for AGENT_MODULE=crewai "
            "(pip install 'crewai==1.15.23')"
        ) from exc

    knowledge = role.knowledge_text()
    backstory = role.backstory
    if knowledge:
        backstory = f"{backstory}\n\n## Role knowledge\n{knowledge}".strip()
    assets_block = role.assets_prompt_block()
    if assets_block:
        backstory = f"{backstory}\n\n## Pack assets\n{assets_block}".strip()
    if role.reports_to:
        backstory = (
            f"{backstory}\n\n## Hierarchy\nYou report to `{role.reports_to}`."
        ).strip()

    agent_tools = tools if tools is not None else build_tools(role.tools)
    kwargs: dict[str, Any] = {
        "role": role.crew_role,
        "goal": role.goal,
        "backstory": backstory,
        "tools": agent_tools,
        "allow_delegation": bool(role.allow_delegation),
        "verbose": False,
        "cache": True,
        "respect_context_window": True,
        "tool_failure_policy": ToolFailurePolicy.WARN,
        "max_iter": min(
            int(role.max_iter),
            max(1, int(max_iterations)) if max_iterations is not None else int(role.max_iter),
        ),
        "llm": llm_id_for_crew(),
        "memory": memory,
        "checkpoint": checkpoint,
        "max_execution_time": max_execution_time,
        "max_retry_limit": max(1, int(max_replans)),
    }
    if role.reasoning:
        from crewai import PlanningConfig

        kwargs["planning_config"] = PlanningConfig(
            reasoning_effort="medium",
            max_replans=max(0, int(max_replans)),
            max_steps=kwargs["max_iter"],
            max_step_iterations=min(kwargs["max_iter"], 10),
        )
    return Agent(**kwargs)


def restore_agent_runtime_policy(
    agent: Any,
    *,
    max_iterations: int,
    max_replans: int,
    max_execution_time: int | None,
    memory: Any | None,
    checkpoint: Any | None,
) -> Any:
    """Reapply catalog/runtime policy omitted by CrewAI checkpoint payloads."""
    from crewai import PlanningConfig

    from orchestrator.crew.roles.registry import RoleRegistry

    identity = str(getattr(agent, "role", "") or "").strip()
    role = next(
        (
            item
            for item in RoleRegistry.shared().list_roles()
            if identity in {item.id, item.label, item.crew_role}
        ),
        None,
    )
    if role is None:
        raise RuntimeError(f"restored CrewAI agent has no matching role pack: {identity!r}")

    limit = min(int(role.max_iter), max(1, int(max_iterations)))
    agent.max_iter = limit
    agent.max_retry_limit = max(1, int(max_replans))
    agent.max_execution_time = max_execution_time
    agent.memory = memory
    agent.checkpoint = checkpoint
    agent.planning_config = (
        PlanningConfig(
            reasoning_effort="medium",
            max_replans=max(0, int(max_replans)),
            max_steps=limit,
            max_step_iterations=min(limit, 10),
        )
        if role.reasoning
        else None
    )
    return agent
