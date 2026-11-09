"""Map an AgentSpec onto a CrewAI Agent. Prompt assembly stays in build_agent_spec."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from orchestrator.config import get_config
from orchestrator.crew.roles.model import RoleSpec, build_agent_spec


def llm_id_for_crew() -> Any:
    """Build the CrewAI ``Agent(llm=...)`` / ``planning_llm=...`` value.

    A bare model string is enough for a provider CrewAI resolves from env
    vars it already knows (OpenRouter's ``OPENROUTER_API_KEY``, etc.). A
    custom endpoint (Ollama, any other OpenAI-compatible base) needs an
    explicit ``crewai.LLM`` instead: CrewAI's native ``ollama``/``ollama_chat``
    route reads its base URL from a ``base_url`` kwarg (or ``OLLAMA_HOST``),
    never from ``api_base`` or a bare model string — passing only the model
    id silently drops the endpoint and falls back to connecting to OpenAI.
    """
    cfg = get_config()
    model = (cfg.litellm_model or "").strip()
    if not model:
        return ""
    base = (cfg.litellm_api_base or "").strip()
    if not base:
        return model
    from crewai.llm import LLM

    return LLM(model=model, base_url=base, api_key=cfg.litellm_api_key or "ollama")


def _to_crewai_tools(names: tuple[str, ...]) -> list[Any]:
    """Convert registered plain functions into CrewAI tools. No schema generator."""
    from orchestrator.crew.tools.catalog import get_tool_spec, known_tool_names

    try:
        from crewai.tools import tool as crewai_tool
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "crewai is required for AGENT_MODULE=crewai (pip install 'crewai>=1.0.0')"
        ) from exc

    out: list[Any] = []
    for name in names:
        spec = get_tool_spec(name)
        if spec is None:
            known = ", ".join(known_tool_names()) or "(none)"
            raise KeyError(f"unknown role tool {name!r}; known: {known}")
        wrapped = crewai_tool(spec.name)(spec.fn)
        if spec.description and hasattr(wrapped, "description"):
            try:
                wrapped.description = spec.description
            except Exception:
                pass
        out.append(wrapped)
    return out


def build_crew_agent(
    role: RoleSpec,
    *,
    tools: list[Any] | None = None,
    skill_ids: Iterable[str] | None = None,
    max_iterations: int | None = None,
    reasoning_effort: str | None = None,
    reasoning_max_attempts: int | None = None,
    collaborate: bool = False,
    dispatch_only: bool = False,
) -> Any:
    """Instantiate a CrewAI ``Agent`` for ``role`` (lazy crewai import).

    Prompt and tool-allowlist assembly lives in ``build_agent_spec``. This
    function only maps that spec onto ``Agent`` kwargs. ``collaborate`` forces
    delegation on, so CrewAI injects its native coworker tools (in-process crews).
    ``tools`` replaces the allowlist with already-built CrewAI tools (the
    hierarchical manager passes ``[]``).
    """
    spec = build_agent_spec(
        role,
        skill_ids=skill_ids,
        max_iterations=max_iterations,
        reasoning_effort=reasoning_effort,
        reasoning_max_attempts=reasoning_max_attempts,
        collaborate=collaborate,
        dispatch_only=dispatch_only,
        tool_names=() if tools is not None else None,
    )
    try:
        from crewai import Agent
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "crewai is required for AGENT_MODULE=crewai (pip install 'crewai>=1.0.0')"
        ) from exc

    agent_tools = tools if tools is not None else _to_crewai_tools(spec.tool_names)
    kwargs: dict[str, Any] = {
        "role": spec.title,
        "goal": spec.goal,
        "backstory": spec.system_prompt,
        "tools": agent_tools,
        "allow_delegation": spec.delegation,
        "verbose": False,
        "max_iter": spec.max_iterations,
        "llm": llm_id_for_crew(),
    }
    if spec.reasoning is not None:
        try:
            from crewai.agent.planning_config import PlanningConfig

            kwargs["planning_config"] = PlanningConfig(
                reasoning_effort=spec.reasoning.effort,
                max_attempts=spec.reasoning.max_attempts,
            )
        except Exception:
            kwargs["reasoning"] = True
            kwargs["max_reasoning_attempts"] = spec.reasoning.max_attempts
    try:
        return Agent(**kwargs)
    except TypeError:
        kwargs.pop("planning_config", None)
        kwargs.pop("reasoning", None)
        kwargs.pop("max_reasoning_attempts", None)
        return Agent(**kwargs)

