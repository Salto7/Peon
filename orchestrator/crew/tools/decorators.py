"""Shared CrewAI tool registration helper."""

from __future__ import annotations

from functools import wraps
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

            @wraps(fn)
            def runtime_aware(*args: Any, **kwargs: Any) -> str:
                from orchestrator.crew.runtime_support import augment_tool_result

                return augment_tool_result(fn(*args, **kwargs))

            wrapped = crewai_tool(name)(runtime_aware)
            if description and hasattr(wrapped, "description"):
                try:
                    wrapped.description = description
                except Exception:
                    pass
            return wrapped

        register_tool(name, factory)
        return fn

    return deco
