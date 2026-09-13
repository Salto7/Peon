"""Job-scoped agent context, ports ABC, and contextvars binding."""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator

from orchestrator.agent.config import AgentRunConfig


class AgentPorts(ABC):
    """Callbacks peon registers so orchestrator never imports Django."""

    @abstractmethod
    def emit(
        self, message_type: str, content: str, *, metadata: dict[str, Any] | None = None
    ) -> None: ...

    @abstractmethod
    def spawn_child(
        self, *, title: str, description: str, skill_names: list[str]
    ) -> str: ...

    @abstractmethod
    def wait_children(
        self, *, job_ids: list[str] | None = None, timeout_seconds: int = 600
    ) -> str: ...

    def list_objectives(self) -> str:
        return "Objectives port not configured."

    def update_objective_status(self, seq: int, status: str, note: str = "") -> str:
        del seq, status, note
        return "update_objective_status port not configured."

    def record_finding(self, **fields: Any) -> str:
        del fields
        return "record_finding port not configured."

    def record_findings(self, findings_json: str) -> str:
        del findings_json
        return "record_findings port not configured."

    def list_findings(self, kind: str = "") -> str:
        del kind
        return "list_findings port not configured."

    def drain_operator_guidance(self) -> list[str]:
        """Return and consume pending operator notes for this job (may be empty)."""
        return []


class NullAgentPorts(AgentPorts):
    """Fail-closed defaults for tests / unbound runs."""

    def emit(
        self, message_type: str, content: str, *, metadata: dict[str, Any] | None = None
    ) -> None:
        del message_type, content, metadata

    def spawn_child(
        self, *, title: str, description: str, skill_names: list[str]
    ) -> str:
        del title, description, skill_names
        raise RuntimeError("spawn_child port not configured")

    def wait_children(
        self, *, job_ids: list[str] | None = None, timeout_seconds: int = 600
    ) -> str:
        del job_ids, timeout_seconds
        raise RuntimeError("wait_children port not configured")


@dataclass
class AgentRunContext:
    """Everything the graph needs for one Job (injected by peon)."""

    job_id: str
    project_id: str = ""
    parent_job_id: str = ""
    workspace: str = ""
    skill_names: list[str] = field(default_factory=list)
    brief: str = ""
    depth: int = 1
    ports: AgentPorts = field(default_factory=NullAgentPorts)
    extras: dict[str, Any] = field(default_factory=dict)


_ctx: ContextVar[AgentRunContext | None] = ContextVar("agent_job_ctx", default=None)
_cfg: ContextVar[AgentRunConfig | None] = ContextVar("agent_job_cfg", default=None)


def get_context() -> AgentRunContext:
    ctx = _ctx.get()
    if ctx is None:
        raise RuntimeError("no agent job context bound")
    return ctx


def get_config() -> AgentRunConfig:
    cfg = _cfg.get()
    if cfg is None:
        return AgentRunConfig()
    return cfg


@contextmanager
def bind_job(ctx: AgentRunContext, config: AgentRunConfig) -> Iterator[None]:
    t_ctx = _ctx.set(ctx)
    t_cfg = _cfg.set(config)
    try:
        yield
    finally:
        _ctx.reset(t_ctx)
        _cfg.reset(t_cfg)
