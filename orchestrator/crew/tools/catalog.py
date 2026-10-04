"""Named CrewAI tools selectable by ROLE.yaml ``tools:`` lists."""

from __future__ import annotations

from typing import Any, Callable

ToolFactory = Callable[[], Any]

_FACTORIES: dict[str, ToolFactory] = {}


def register_tool(name: str, factory: ToolFactory) -> ToolFactory:
    key = (name or "").strip()
    if not key:
        raise ValueError("tool name required")
    _FACTORIES[key] = factory
    return factory


def known_tool_names() -> list[str]:
    _ensure()
    return sorted(_FACTORIES)


def build_tools(names: tuple[str, ...] | list[str]) -> list[Any]:
    """Instantiate allowlisted tools; unknown names raise."""
    _ensure()
    out: list[Any] = []
    for raw in names or ():
        name = str(raw or "").strip()
        if not name:
            continue
        factory = _FACTORIES.get(name)
        if factory is None:
            known = ", ".join(known_tool_names()) or "(none)"
            raise KeyError(f"unknown role tool {name!r}; known: {known}")
        out.append(factory())
    return out


def _ensure() -> None:
    if _FACTORIES:
        return
    # Side-effect registration
    from orchestrator.crew.tools import findings as _findings  # noqa: F401
    from orchestrator.crew.tools import roe as _roe  # noqa: F401
    from orchestrator.crew.tools import sandbox as _sandbox  # noqa: F401
