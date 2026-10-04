"""Role catalog DTOs (data under ``roles/<id>/ROLE.yaml``)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from orchestrator.crew.roles.resources import list_resource_files, resolve_resource


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
    max_iter: int = 20
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
        return tuple(list_resource_files(self.root))

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
