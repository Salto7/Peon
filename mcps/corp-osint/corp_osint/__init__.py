"""corp-osint: plugin-based corporate OSINT toolkit."""

from .config import PipelineConfig
from .log import get_logger, setup_logging
from .plugins.pipeline import CorpOsintPipeline
from .plugin import Plugin
from .plugins import PLUGIN_REGISTRY, create_plugin, list_plugins
from .runtime import run_pipeline, run_plugin

__all__ = [
    "CorpOsintPipeline",
    "PipelineConfig",
    "Plugin",
    "PLUGIN_REGISTRY",
    "create_plugin",
    "list_plugins",
    "get_logger",
    "setup_logging",
    "run_pipeline",
    "run_plugin",
]
__version__ = "3.0.0"
