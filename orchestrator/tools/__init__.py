"""CLI tool catalog + install resolver.

Shared capability tool defs live in ``orchestrator.capabilities.tools``.
CrewAI role tools live in ``orchestrator.crew.tools``.
"""

from orchestrator.tools.catalog import CatalogTool, ToolCatalog

__all__ = [
    "CatalogTool",
    "CatalogProvisioner",
    "InstallResolver",
    "ToolCatalog",
]


def __getattr__(name: str):
    if name == "InstallResolver":
        from orchestrator.tools.install import InstallResolver

        return InstallResolver
    if name == "CatalogProvisioner":
        from orchestrator.tools.catalog import CatalogProvisioner

        return CatalogProvisioner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
