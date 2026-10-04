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
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "crewai is required for AGENT_MODULE=crewai "
            "(pip install 'crewai>=1.0.0')"
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
