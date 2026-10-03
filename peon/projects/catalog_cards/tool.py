"""UI cards for sandbox tools (tools/catalog YAML)."""

from __future__ import annotations

from typing import Any

from orchestrator.tools.catalog import CatalogTool, ToolCatalog
from peon.projects.catalog_cards_base import CatalogCardsBase


class ToolCards(CatalogCardsBase):
    """UI cards for sandbox tools (``tools/catalog`` YAML)."""

    SECTION = "tools"
    LABEL = "Tools"
    RELOAD_URL_NAME = "catalog_api_tools_reload"
    RELOAD_TITLE = "Rescan TOOLS_CATALOG_DIR via ToolCatalog.reload_tools"

    @classmethod
    def reload(cls) -> dict[str, list]:
        return ToolCatalog.shared().reload_tools()

    @classmethod
    def card(
        cls,
        *,
        id: str,
        name: str = "",
        description: str = "",
        tier: str = "",
        binary: str = "",
        binaries: list | None = None,
        skills: list | None = None,
        tags: list | None = None,
        install: list | None = None,
        desc_limit: int | None = None,
        tag_limit: int | None = None,
    ) -> dict:
        tool_id = cls.text(id)
        steps = list(install or [])
        has_command = any(
            isinstance(s, dict)
            and (
                str(s.get("type") or "").strip().lower()
                in {"custom", "command", "shell", "run", "bash", "script"}
                or (not s.get("type") and s.get("command"))
            )
            for s in steps
        )
        return {
            "id": tool_id,
            **cls.base(
                name=name or tool_id,
                description=description,
                tags=tags,
                desc_limit=desc_limit,
                tag_limit=tag_limit,
            ),
            "tier": cls.text(tier),
            "binary": cls.text(binary),
            "binaries": cls.labels(binaries),
            "skills": cls.labels(skills),
            "install_types": [
                str(s.get("type") or ("custom" if s.get("command") else "")).strip()
                for s in steps
                if isinstance(s, dict)
            ],
            "has_custom_install": has_command,
        }

    @classmethod
    def from_tool(cls, tool: CatalogTool) -> dict:
        return cls.card(
            id=tool.id,
            name=tool.name,
            description=tool.description or "",
            tier=tool.tier or "",
            binary=tool.binary or "",
            binaries=list(tool.binaries or []),
            skills=list(tool.skills or []),
            tags=list(tool.tags or []),
            install=list(tool.install or []),
        )

    @classmethod
    def catalog(cls, *, include_image: bool = True, **kwargs: Any) -> list[dict]:
        del kwargs
        tools = sorted(ToolCatalog.shared().all().values(), key=lambda t: t.id)
        out: list[dict] = []
        for t in tools:
            if not include_image and t.is_image_tier:
                continue
            out.append(cls.from_tool(t))
        return out

