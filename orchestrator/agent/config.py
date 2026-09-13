"""Immutable run governors for one Job agent loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class AgentRunConfig:
    """Caps passed from host settings / env into ``orchestrator.agent``."""

    max_failure_replans: int = 2
    max_iterations: int = 40
    max_subagents: int = 4
    max_subagent_depth: int = 2
    runtime_enabled: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "max_failure_replans", max(0, int(self.max_failure_replans))
        )
        object.__setattr__(self, "max_iterations", max(1, int(self.max_iterations)))
        object.__setattr__(self, "max_subagents", max(0, int(self.max_subagents)))
        object.__setattr__(
            self, "max_subagent_depth", max(1, int(self.max_subagent_depth))
        )


def agent_run_config_from_mapping(data: Mapping[str, object]) -> AgentRunConfig:
    """Build config from a plain mapping (e.g. Django settings attrs)."""

    def _int(key: str, default: int) -> int:
        raw = data.get(key, default)
        try:
            return int(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default

    def _bool(key: str, default: bool = False) -> bool:
        raw = data.get(key, default)
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    return AgentRunConfig(
        max_failure_replans=_int("AGENT_MAX_FAILURE_REPLANS", 2),
        max_iterations=_int("AGENT_MAX_ITERATIONS", 40),
        max_subagents=_int("AGENT_MAX_SUBAGENTS", 4),
        max_subagent_depth=_int("AGENT_MAX_SUBAGENT_DEPTH", 2),
        runtime_enabled=_bool("AGENT_RUNTIME_ENABLED", True),
    )
