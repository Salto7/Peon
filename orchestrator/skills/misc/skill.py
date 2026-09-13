"""Skill dataclass."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestrator.skills.misc.utils import CORE_SKILLS, is_protected


@dataclass
class Skill:
    name: str
    description: str
    instructions: str
    tools: list[str] = field(default_factory=list)
    path: Path = field(default_factory=Path)
    lifecycle: str | None = None
    max_iterations: int | None = None
    category: str | None = None
    tags: list[str] = field(default_factory=list)
    toolkit: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    jobable: bool = True
    manually_created: bool = False
    direct_answer: bool = False
    protected: bool = False
    source: str = "local"
    manifest_path: Path = field(default_factory=Path)
    mtime: float = 0.0
    assets: list[str] = field(default_factory=list)
    mcp_servers: list = field(default_factory=list)

    @classmethod
    def from_parsed(
        cls,
        parsed: dict[str, Any],
        *,
        root: Path,
        source: str,
        manifest: Path,
        assets: list[str] | None = None,
        mcp_servers: list | None = None,
    ) -> Skill:
        return cls(
            name=parsed["name"],
            description=parsed["description"],
            instructions=parsed["instructions"],
            tools=parsed.get("tools") or [],
            path=root,
            lifecycle=parsed.get("lifecycle"),
            max_iterations=parsed.get("max_iterations"),
            category=parsed.get("category") or "",
            tags=list(parsed.get("tags") or []),
            toolkit=list(parsed.get("toolkit") or []),
            aliases=list(parsed.get("aliases") or []),
            jobable=bool(parsed.get("jobable", True)),
            manually_created=bool(parsed.get("manually_created", False)),
            direct_answer=bool(parsed.get("direct_answer", False)),
            protected=bool(parsed.get("protected", False)),
            source=source,
            manifest_path=manifest,
            mtime=manifest.stat().st_mtime,
            assets=list(assets or []),
            mcp_servers=list(mcp_servers or []),
        )

    @property
    def is_builtin(self) -> bool:
        return self.name in CORE_SKILLS

    @property
    def is_protected(self) -> bool:
        return is_protected(name=self.name, protected=self.protected)

    @property
    def is_mcp(self) -> bool:
        """True when the skill itself advertises MCP (servers, name, or mcp_* tools)."""
        if self.mcp_servers:
            return True
        if self.name.startswith("mcp-"):
            return True
        return any(str(t).startswith("mcp_") for t in (self.tools or []))

    @property
    def skill_dir(self) -> Path:
        return self.manifest_path.parent

    def to_catalog_entry(self) -> dict[str, Any]:
        """Compact projection shared by load discover and JSON catalog."""
        return {
            "name": self.name,
            "description": self.description,
            "location": str(self.manifest_path),
            "tags": list(self.tags or []),
            "category": self.category or "",
            "jobable": bool(self.jobable),
        }

    def to_catalog_dict(self) -> dict[str, Any]:
        return {
            **self.to_catalog_entry(),
            "tools": self.tools,
            "lifecycle": self.lifecycle,
            "toolkit": list(self.toolkit or []),
            "aliases": list(self.aliases or []),
            "manually_created": self.manually_created,
            "source": self.source,
            "assets": self.assets,
            "builtin": self.is_builtin,
            "protected": self.is_protected,
        }
