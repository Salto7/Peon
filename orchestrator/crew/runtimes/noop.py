"""Fail-closed Crew runtime until the Project Flow is wired (Phase C)."""

from __future__ import annotations

from orchestrator.crew.runtime_base import (
    CrewRunRequest,
    CrewRunResult,
    CrewRuntimeBase,
)


class NoopCrewRuntime(CrewRuntimeBase):
    """Placeholder so AGENT_MODULE=crewai boots before Flow implementation."""

    def start(self, request: CrewRunRequest) -> CrewRunResult:
        action = "replan" if request.replan else ("resume" if request.resume else "start")
        return CrewRunResult(
            ok=False,
            error=(
                f"CrewAI project runtime not implemented yet "
                f"(noop; action={action} project={request.project_id})"
            ),
            flow_id=request.flow_id,
            status="stopped",
        )
