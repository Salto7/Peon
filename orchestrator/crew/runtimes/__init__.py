"""Concrete crew / job runtimes (lazy where heavy)."""

from __future__ import annotations

from typing import Any

__all__ = ["CrewAIAgentExecutor"]


def __getattr__(name: str) -> Any:
    if name == "CrewAIAgentExecutor":
        # deferred: optional heavy crewai runtime
        from orchestrator.crew.runtimes.job_crewai import CrewAIAgentExecutor

        return CrewAIAgentExecutor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
