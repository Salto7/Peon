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


def crew_tool(name: str, description: str) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """Register a function as a named CrewAI tool factory."""

    def deco(fn: Callable[..., str]) -> Callable[..., str]:
        if description and not (fn.__doc__ or "").strip():
            fn.__doc__ = description

        def factory() -> Any:
            try:
                # deferred: optional heavy crewai
                from crewai.tools import tool as crewai_tool
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "crewai is required for AGENT_MODULE=crewai "
                    "(pip install 'crewai>=1.0.0')"
                ) from exc

            wrapped = crewai_tool(name)(fn)
            if description and hasattr(wrapped, "description"):
                try:
                    wrapped.description = description
                except Exception:
                    pass
            return wrapped

        register_tool(name, factory)
        return fn

    return deco


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
    # circular: adapters → crew_tool from this module
    from orchestrator.crew.tools import adapters as _adapters  # noqa: F401
