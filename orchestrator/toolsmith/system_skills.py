"""Which catalog skills supply Toolsmith's own authoring prompts.

Toolsmith's engines are driven by two prompt packs held as catalog skills.
Their ids are platform settings (so a rename or replacement is a
configuration change, not a code change); the defaults are the shipped ids.
"""

from __future__ import annotations

_DEFAULT_TOOLS = "tools-suggestor"
_DEFAULT_WRITER = "skill-writer"


def _setting(key: str, default: str) -> str:
    try:
        from peon.projects.runtime_settings import PeonSettings

        value = str(PeonSettings.get_str(key, default) or "").strip()
    except Exception:  # noqa: BLE001 — settings unavailable (tests, bootstrap)
        value = ""
    return value or default


def tools_suggestor_skill() -> str:
    """Skill whose prompt turns a request into a tool-catalog recipe."""
    return _setting("AUTHORING_TOOLS_SKILL", _DEFAULT_TOOLS)


def skill_writer_skill() -> str:
    """Skill whose prompt drafts new skills."""
    return _setting("AUTHORING_SKILL_WRITER", _DEFAULT_WRITER)
