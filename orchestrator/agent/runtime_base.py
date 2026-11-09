"""Agent runtime ABC and run DTOs (one Job = one tool loop)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from orchestrator.agent.config import AgentRunConfig
from orchestrator.agent.job import JobScope


@dataclass(frozen=True)
class AgentRunResult:
    ok: bool
    output: str = ""
    error: str = ""
    iterations: int = 0
    replans: int = 0


@dataclass(frozen=True)
class AgentRunRequest:
    """Input for start/resume of one Job agent run."""

    scope: JobScope
    config: AgentRunConfig
    resume: bool = False
    steer: str = ""


class AgentExecutorBase(ABC):
    """Framework-agnostic Job agent execution."""

    @abstractmethod
    def start(self, request: AgentRunRequest) -> AgentRunResult:
        """Run (or continue) the agent for ``request.scope.job_id``."""

    def available(self) -> bool:
        """True when this backend's framework can actually run here.

        Default ``True``; a backend whose framework may be missing (an
        optional dependency) overrides this with a real check so
        ``agent_backend_check`` can fail fast instead of erroring on the
        first job.
        """
        return True

    def resume(self, request: AgentRunRequest) -> AgentRunResult:
        return self.start(
            AgentRunRequest(
                scope=request.scope,
                config=request.config,
                resume=True,
                steer=request.steer,
            )
        )


_DEFAULT: AgentExecutorBase | None = None


def get_agent_runtime() -> AgentExecutorBase:
    """Return the backend for ``AGENT_MODULE`` (crewai)."""
    global _DEFAULT
    if _DEFAULT is None:
        # Lazy: orchestrator.agent.registry imports AgentExecutorBase from
        # this module at module scope, so a top-level import here would be
        # a real import cycle, not just a stale warning.
        from orchestrator.agent.registry import get_job_runtime

        _DEFAULT = get_job_runtime()
    return _DEFAULT


def set_agent_runtime(runtime: AgentExecutorBase | None) -> None:
    global _DEFAULT
    _DEFAULT = runtime
    if runtime is None:
        # Lazy for the same reason as get_agent_runtime() above.
        from orchestrator.agent.registry import reset_agent_modules

        reset_agent_modules()


def run_agent(
    scope: JobScope,
    config: AgentRunConfig,
    *,
    resume: bool = False,
    steer: str = "",
) -> AgentRunResult:
    """Control-plane entry: run one Job via the process agent runtime."""
    return get_agent_runtime().start(
        AgentRunRequest(scope=scope, config=config, resume=resume, steer=steer)
    )
