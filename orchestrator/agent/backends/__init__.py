"""Agent backends. One package per framework, one registry line each."""

from __future__ import annotations

from abc import abstractmethod

from orchestrator.agent.runtime_base import (
    AgentExecutorBase,
    AgentRunRequest,
    AgentRunResult,
)


def project_crew_requested(scope) -> bool:
    """True only for an explicit project-crew run, never merely for a manager role."""
    extras = getattr(scope, "extras", None) or {}
    return str(extras.get("crew_mode") or "").strip().lower() == "project"


class AgentBackend(AgentExecutorBase):
    """One agent framework.

    ``supports_project_crew`` opts into the hierarchical project crew. A backend
    without it runs the manager as a normal single-role job.
    """

    supports_project_crew: bool = False

    def warm(self) -> None:
        """Pay the framework import cost before the first job. Default is a no-op."""
        return None

    def start(self, request: AgentRunRequest) -> AgentRunResult:
        if self.supports_project_crew and project_crew_requested(request.scope):
            return self.run_project(request)
        return self.run_job(request)

    @abstractmethod
    def run_job(self, request: AgentRunRequest) -> AgentRunResult:
        """Run one role for ``request.scope``."""

    @abstractmethod
    def run_project(self, request: AgentRunRequest) -> AgentRunResult:
        """Run a project crew. Only called when ``supports_project_crew`` is set."""
