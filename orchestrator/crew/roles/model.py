"""Role catalog DTOs + CrewAI Agent factory.

Roles come from the platform role store (id / kind) and are decorated from
affinity-bound skill packs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from orchestrator.config import get_config
from orchestrator.crew.tools import build_tools, with_collab_tools


@dataclass(frozen=True)
class RoleSpec:
    """CrewAI agent decoration + skill group."""

    id: str
    label: str
    crew_role: str
    goal: str
    backstory: str
    tools: tuple[str, ...] = ()
    allow_binaries: tuple[str, ...] = ()
    reports_to: str = ""
    capabilities: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    kind: str = "technical"  # engagement-pm | analyzer | technical
    allow_delegation: bool = False
    reasoning: bool = False
    advanced_reasoning: bool = False
    max_iter: int = 20

    @property
    def is_manager(self) -> bool:
        if self.kind == "engagement-pm":
            return True
        if self.kind in {"analyzer", "technical"}:
            return False
        return bool(self.allow_delegation) and not self.reports_to

    @property
    def is_analyzer(self) -> bool:
        if self.kind == "analyzer":
            return True
        # Explicit technical/PM kinds must not flip via skill capability tags
        # (e.g. a recon skill tagged "report").
        if self.kind in {"engagement-pm", "technical"}:
            return False
        caps = {c.lower() for c in self.capabilities}
        return "report" in caps or "analyzer" in caps

    @property
    def is_technical(self) -> bool:
        if self.kind == "technical":
            return True
        if self.kind in {"engagement-pm", "analyzer"}:
            return False
        return not self.is_manager and not self.is_analyzer


def llm_id_for_crew() -> str:
    """Map Peon LiteLLM model id into a CrewAI-friendly llm string."""
    cfg = get_config()
    model = (cfg.litellm_model or "").strip()
    return model or "openrouter/openai/gpt-5.6-luna"


def build_crew_agent(
    role: RoleSpec,
    *,
    tools: list[Any] | None = None,
    skill_ids: Iterable[str] | None = None,
    max_iterations: int | None = None,
    reasoning_effort: str | None = None,
    reasoning_max_attempts: int | None = None,
) -> Any:
    """Instantiate a CrewAI ``Agent`` for ``role`` (lazy crewai import)."""
    try:
        from crewai import Agent
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "crewai is required for AGENT_MODULE=crewai "
            "(pip install 'crewai>=1.0.0')"
        ) from exc

    from orchestrator.skills.bind import (
        effective_tools,
        progressive_tool_names,
        resolve_skill_ids,
        skills_prompt_block,
    )

    selected = resolve_skill_ids(role, skill_ids)
    backstory = role.backstory
    skills_block = skills_prompt_block(selected, role_id=role.id)
    if skills_block:
        backstory = f"{backstory}\n\n{skills_block}".strip()
    if role.reports_to:
        backstory = (
            f"{backstory}\n\n## Hierarchy\nYou report to `{role.reports_to}`."
        ).strip()
    if role.is_technical or role.is_manager:
        backstory = (
            f"{backstory}\n\n## Collaboration\n"
            "Engagement PM owns the objective bookend; you govern **in-lane** "
            "work and **must parallelize** via Dramatiq child jobs. For "
            "multi-host/CIDR/target lists or multi-skill follow-ons: "
            "`spawn_work` (empty `role_id` = this expert) with focused "
            "`skill_ids` + `scope_slice`, then `wait_for_work` before "
            "synthesizing. Do **not** ask the PM to replan for follow-ups "
            "already in your skill group. Solo in-process `bash` only for "
            "one host/URL or a quick verify. Out-of-lane work, unknown roles, "
            "or RoE ambiguity: `list_roles` then spawn another catalog role, or "
            "`ask_operator`. Never invent role or skill ids."
        ).strip()

    cfg = get_config()
    role_iters = max(1, int(role.max_iter))
    cap = max(
        1,
        int(
            max_iterations
            if max_iterations is not None
            else cfg.agent_max_iterations
        ),
    )
    tool_names = effective_tools(role, selected) if tools is None else None
    if tools is not None:
        agent_tools = tools
    else:
        names = list(with_collab_tools(role, tool_names=tool_names))
        for extra in progressive_tool_names(selected, role_id=role.id):
            if extra not in names:
                names.append(extra)
        agent_tools = build_tools(names)
    kwargs: dict[str, Any] = {
        "role": role.crew_role,
        "goal": role.goal,
        "backstory": backstory,
        "tools": agent_tools,
        "allow_delegation": bool(role.allow_delegation or role.is_manager),
        "verbose": False,
        "max_iter": min(role_iters, cap),
        "llm": llm_id_for_crew(),
    }
    if role.reasoning:
        effort = (
            (reasoning_effort or cfg.crew_reasoning_effort or "low").strip().lower()
        )
        if effort not in {"low", "medium", "high"}:
            effort = "low"
        attempts = max(
            1,
            min(
                5,
                int(
                    reasoning_max_attempts
                    if reasoning_max_attempts is not None
                    else cfg.crew_reasoning_max_attempts
                ),
            ),
        )
        try:
            from crewai.agent.planning_config import PlanningConfig

            kwargs["planning_config"] = PlanningConfig(
                reasoning_effort=effort,
                max_attempts=attempts,
            )
        except Exception:
            kwargs["reasoning"] = True
            kwargs["max_reasoning_attempts"] = attempts
    try:
        return Agent(**kwargs)
    except TypeError:
        kwargs.pop("planning_config", None)
        kwargs.pop("reasoning", None)
        kwargs.pop("max_reasoning_attempts", None)
        return Agent(**kwargs)
