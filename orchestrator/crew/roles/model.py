"""Role catalog DTOs + CrewAI Agent factory (data under ``roles/<id>/ROLE.yaml``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestrator.config import get_config
from orchestrator.packs import list_role_resource_files, resolve_resource
from orchestrator.crew.tools import build_tools


@dataclass(frozen=True)
class RoleSpec:
    """One CrewAI-facing role definition loaded from disk."""

    id: str
    label: str
    crew_role: str
    goal: str
    backstory: str
    tools: tuple[str, ...] = ()
    allow_binaries: tuple[str, ...] = ()
    reports_to: str = ""
    capabilities: tuple[str, ...] = ()
    knowledge_files: tuple[str, ...] = ()
    assets: tuple[str, ...] = ()
    mode: str = "engagement"  # engagement | authoring
    requires_roe: bool = False
    allow_delegation: bool = False
    reasoning: bool = False
    advanced_reasoning: bool = False
    max_iter: int = 20
    # Learn/Toolsmith: engine + task→prompt paths (role-owned, not Peon-hardcoded).
    authoring_engine: str = ""
    authoring_bootstrap: str = ""
    authoring_tasks: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    root: Path | None = None

    @property
    def is_manager(self) -> bool:
        """Engagement lead: can delegate and has no supervisor."""
        return bool(self.allow_delegation) and not self.reports_to

    @property
    def is_analyzer(self) -> bool:
        caps = {c.lower() for c in self.capabilities}
        return "report" in caps or "analyzer" in caps

    @property
    def is_authoring(self) -> bool:
        if (self.mode or "").strip().lower() == "authoring":
            return True
        caps = {c.lower() for c in self.capabilities}
        return bool(caps & {"authoring", "draft", "learn"})

    def knowledge_text(self) -> str:
        if not self.root or not self.knowledge_files:
            return ""
        chunks: list[str] = []
        for name in self.knowledge_files:
            path = self.root / name
            if path.is_file():
                chunks.append(path.read_text(encoding="utf-8").strip())
        return "\n\n".join(c for c in chunks if c)

    def refresh_assets(self) -> tuple[str, ...]:
        if not self.root:
            return self.assets
        return tuple(list_role_resource_files(self.root))

    def resolve_asset(self, relative_path: str) -> Path | None:
        if not self.root:
            return None
        return resolve_resource(self.root, relative_path)

    def assets_prompt_block(self) -> str:
        """Paths the agent may use for custom work under this role pack."""
        names = list(self.assets) or list(self.refresh_assets())
        if not names:
            return ""
        lines = [
            "Role pack assets (read/run from the role directory; copy into "
            "workspace/ when you need to customize):",
            *[f"- {n}" for n in names[:40]],
        ]
        return "\n".join(lines)

    @property
    def is_learn_author(self) -> bool:
        caps = {c.lower() for c in self.capabilities}
        return bool(self.authoring_engine) or (
            bool(caps & {"opencode", "authoring"}) and "writer" in caps
        )

    def authoring_task_path(self, task: str) -> str:
        """Relative prompt path for a Learn task (tool / role / tool_replan)."""
        key = (task or "").strip().lower()
        for name, rel in self.authoring_tasks:
            if name == key:
                return rel
        return f"references/PROMPT_{key.upper()}.md"

    def authoring_prompt_text(self, task: str) -> str:
        """Load the system prompt for a Learn task from this role pack."""
        rel = self.authoring_task_path(task)
        path = self.resolve_asset(rel)
        if path is None and self.root is not None:
            candidate = self.root / rel
            if candidate.is_file():
                path = candidate
        if path is None or not path.is_file():
            raise FileNotFoundError(
                f"role {self.id!r} missing authoring prompt for task {task!r}: {rel}"
            )
        return path.read_text(encoding="utf-8")

    def authoring_bootstrap_text(self) -> str:
        """Optional lab bootstrap script contents (e.g. install OpenCode)."""
        rel = (self.authoring_bootstrap or "").strip()
        if not rel or not self.root:
            return ""
        path = self.resolve_asset(rel) or (self.root / rel)
        if path.is_file():
            return path.read_text(encoding="utf-8")
        return ""


def llm_id_for_crew() -> str:
    """Map Peon LiteLLM model id into a CrewAI-friendly llm string."""
    cfg = get_config()
    model = (cfg.litellm_model or "").strip()
    return model or "openrouter/openai/gpt-4o-mini"


def build_crew_agent(role: RoleSpec, *, tools: list[Any] | None = None) -> Any:
    """Instantiate a CrewAI ``Agent`` for ``role`` (lazy crewai import)."""
    try:
        # deferred: optional heavy crewai
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
        "max_iter": int(role.max_iter),
        "llm": llm_id_for_crew(),
    }
    # ``advanced_reasoning`` is crew-level planning only (engagement.py).
    # Agent planning stays off unless ROLE.yaml sets ``reasoning: true``, and
    # then uses low-effort / single-attempt config — never bare reasoning=True
    # (CrewAI medium effort + unbounded refine = multi-minute bookends).
    if role.reasoning:
        try:
            from crewai.agent.planning_config import PlanningConfig

            kwargs["planning_config"] = PlanningConfig(
                reasoning_effort="low",
                max_attempts=1,
            )
        except Exception:
            kwargs["reasoning"] = True
            kwargs["max_reasoning_attempts"] = 1
    try:
        return Agent(**kwargs)
    except TypeError:
        kwargs.pop("planning_config", None)
        kwargs.pop("reasoning", None)
        kwargs.pop("max_reasoning_attempts", None)
        return Agent(**kwargs)
