"""run_agent follows the process runtime override, then reload on reset."""

from __future__ import annotations

from orchestrator.agent.config import AgentRunConfig
from orchestrator.agent.job import JobScope
from orchestrator.agent.runtime_base import (
    AgentExecutorBase,
    AgentRunResult,
    get_agent_runtime,
    run_agent,
    set_agent_runtime,
)


class FakeExecutor(AgentExecutorBase):
    def start(self, request):
        return AgentRunResult(
            ok=True,
            output=f"ran:{request.scope.job_id}:{request.steer}",
            iterations=4,
        )


def test_set_agent_runtime_returns_fake_then_reloads_configured_module():
    scope = JobScope(job_id="job-1", bridge=object())
    config = AgentRunConfig()
    fake = FakeExecutor()
    set_agent_runtime(fake)
    try:
        result = run_agent(scope, config, steer="hold")
        assert result.ok is True
        assert result.output == "ran:job-1:hold"
        assert result.iterations == 4
        assert get_agent_runtime() is fake

        set_agent_runtime(None)
        loaded = get_agent_runtime()
        assert loaded is not fake
        assert type(loaded).__name__ == "CrewAIAgentExecutor"
        assert get_agent_runtime() is loaded
    finally:
        set_agent_runtime(None)
