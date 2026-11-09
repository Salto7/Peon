"""In-test backend. Not a second framework and not part of the registry."""

from __future__ import annotations

from orchestrator.agent.backends import AgentBackend
from orchestrator.agent.runtime_base import AgentRunRequest, AgentRunResult


class FakeBackend(AgentBackend):
    supports_project_crew = False

    def __init__(self) -> None:
        self.warm_calls = 0

    def warm(self) -> None:
        self.warm_calls += 1

    def run_job(self, request: AgentRunRequest) -> AgentRunResult:
        return AgentRunResult(ok=True, output=f"job:{request.scope.job_id}", iterations=2)

    def run_project(self, request: AgentRunRequest) -> AgentRunResult:
        return AgentRunResult(
            ok=True, output=f"project:{request.scope.job_id}", iterations=3
        )
