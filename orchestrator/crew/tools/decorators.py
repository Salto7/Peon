"""Shared CrewAI tool registration helper."""

from __future__ import annotations

from typing import Any, Callable

from orchestrator.crew.tools.catalog import register_handler, register_tool


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

            from orchestrator.crew.tools.registered import RegisteredCrewTool

            schema = crewai_tool(name)(fn).args_schema
            return RegisteredCrewTool(
                name=name,
                description=description or (fn.__doc__ or name),
                args_schema=schema,
                handler_name=name,
            )

        register_tool(name, factory)
        register_handler(name, fn)
        return fn

    return deco
