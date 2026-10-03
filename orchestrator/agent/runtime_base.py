"""Agent runtime ABC and run DTOs (one Job = one tool loop)."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.agent.config import AgentRunConfig
from orchestrator.agent.job import JobScope
from orchestrator.utils.service import SharedServiceBase

logger = logging.getLogger(__name__)


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


class AgentRuntimeBase(ABC):
    """Framework-agnostic Job agent execution."""

    @abstractmethod
    def start(self, request: AgentRunRequest) -> AgentRunResult:
        """Run (or continue) the agent for ``request.scope.job_id``."""

    def resume(self, request: AgentRunRequest) -> AgentRunResult:
        return self.start(
            AgentRunRequest(
                scope=request.scope,
                config=request.config,
                resume=True,
                steer=request.steer,
            )
        )


class CheckpointFactory(SharedServiceBase):
    """LangGraph checkpointer for Job ``thread_id``s (prefer official savers)."""

    def __init__(self) -> None:
        self._memory: Any | None = None
        self._sqlite: Any | None = None
        self._sqlite_path: str = ""

    def get(self, *, backend: str = "auto", sqlite_path: str | Path | None = None) -> Any:
        mode = (backend or "auto").strip().lower()
        path = str(sqlite_path or "").strip()
        if mode in {"sqlite", "auto"} and path:
            saver = self._sqlite_saver(path)
            if saver is not None:
                return saver
            if mode == "sqlite":
                logger.warning(
                    "Sqlite checkpointer unavailable; falling back to MemorySaver"
                )
        return self._memory_saver()

    def _memory_saver(self) -> Any:
        if self._memory is None:
            from langgraph.checkpoint.memory import MemorySaver

            self._memory = MemorySaver()
        return self._memory

    def _sqlite_saver(self, path: str) -> Any | None:
        if self._sqlite is not None and self._sqlite_path == path:
            return self._sqlite
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError:
            return None
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        try:
            import sqlite3

            conn = sqlite3.connect(path, check_same_thread=False)
            self._sqlite = SqliteSaver(conn)
            self._sqlite_path = path
            return self._sqlite
        except Exception as exc:
            logger.warning("SqliteSaver init failed (%s); using MemorySaver", exc)
            return None


_DEFAULT: AgentRuntimeBase | None = None


def get_agent_runtime() -> AgentRuntimeBase:
    global _DEFAULT
    if _DEFAULT is None:
        from orchestrator.agent.runtimes.langgraph import LangGraphAgentRuntime

        _DEFAULT = LangGraphAgentRuntime()
    return _DEFAULT


def set_agent_runtime(runtime: AgentRuntimeBase | None) -> None:
    global _DEFAULT
    _DEFAULT = runtime


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
