"""corp-osint: plugin-based corporate OSINT toolkit."""

from .config import PipelineConfig
from .log import get_logger, setup_logging
from .pipeline import CorpOsintPipeline
from .plugin import Plugin
from .plugins import PLUGIN_REGISTRY, create_plugin, list_plugins
from .report import compact_report, ensure_summary, finalize_report

__all__ = [
    "CorpOsintPipeline",
    "PipelineConfig",
    "Plugin",
    "PLUGIN_REGISTRY",
    "create_plugin",
    "list_plugins",
    "get_logger",
    "setup_logging",
    "compact_report",
    "ensure_summary",
    "finalize_report",
]
__version__ = "3.0.0"
