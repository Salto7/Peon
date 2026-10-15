"""UI cards for MCP servers (platform catalog + ``mcps/``)."""

from __future__ import annotations

from typing import Any

from peon.projects.catalog_cards.base import CatalogCardsBase
from peon.projects.platform_catalog import PlatformCatalog


class McpCards(CatalogCardsBase):
    """UI cards for CatalogMcp rows."""

    SECTION = "mcp"
    LABEL = "MCP servers"
    RELOAD_URL_NAME = "catalog_api_mcp_reload"
    RELOAD_TITLE = "Rescan mcps/ into the platform catalog"

    @classmethod
    def reload(cls) -> dict[str, list]:
        return PlatformCatalog.shared().sync_mcps_from_disk()

    @classmethod
    def reload_button(cls) -> dict[str, str]:
        btn = super().reload_button()
        btn["label"] = "Refresh MCP"
        return btn

    @classmethod
    def catalog(cls, **kwargs: Any) -> list[dict]:
        del kwargs
        return []
