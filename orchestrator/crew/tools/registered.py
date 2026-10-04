"""Checkpoint-safe CrewAI tool backed by the dynamic tool catalog."""

from __future__ import annotations

from typing import Any

from crewai.tools import BaseTool
from pydantic import Field


class RegisteredCrewTool(BaseTool):
    """Resolve a catalog handler by name instead of serializing a function closure."""

    handler_name: str = Field(description="Registered handler key in the Crew tool catalog.")

    def _run(self, **kwargs: Any) -> Any:
        from orchestrator.crew.runtime_support import augment_tool_result
        from orchestrator.crew.tools.catalog import handler_for

        result = handler_for(self.handler_name)(**kwargs)
        return augment_tool_result(result)
