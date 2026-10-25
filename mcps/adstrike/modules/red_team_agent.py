"""
modules/red_team_agent.py — backward-compatible shim.

The implementation has been refactored into the modules.agent package:

  modules/agent/
    __init__.py   — package entry point, exports run()
    constants.py  — all configuration constants
    backends.py   — Ollama + Claude API adapters
    logger.py     — AgentMarkdownLog class
    _core.py      — core implementation (utils, exec helpers, intel,
                     system prompts, tool handlers, orchestration loops)

This file is kept so the main menu dispatch (importlib.import_module +
mod.run()) continues to work without any changes to main.py.
Do not add code here — edit the package submodules instead.
"""
from modules.agent._core import run as _agent_run  # noqa: F401


def run():
    """Thin shim — delegates to modules.agent._core.run."""
    _agent_run()
