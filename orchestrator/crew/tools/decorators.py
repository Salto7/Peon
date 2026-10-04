"""Shared CrewAI tool registration helper."""

from __future__ import annotations

from typing import Any, Callable

from orchestrator.crew.tools.catalog import register_tool


def crew_tool(name: str, description: str) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """Register a function as a named CrewAI tool factory."""

    def deco(fn: Callable[..., str]) -> Callable[..., str]:
        if description and not (fn.__doc__ or "").strip():
            fn.__doc__ = description

        def factory() -> Any:
            try:
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
