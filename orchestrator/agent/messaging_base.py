"""Agent↔agent message DTOs (host implements persistence; A2A adapter later).

Not the Job tool loop — see ``runtime``. LLM tools reach messaging via ``AgentBridgeBase``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

MessageType = Literal["request", "inform", "handoff", "challenge"]


@dataclass(frozen=True)
class AgentMessage:
    """Neutral DTO — map to A2A tasks later without changing callers."""

    id: str
    objective_id: str
    from_job_id: str
    to_job_id: str  # empty = broadcast to objective peers
    type: MessageType
    body: str
    artifact_refs: tuple[str, ...] = ()
    created_at: datetime | None = None


class AgentMessagingPortBase(ABC):
    """Control-plane mediated messages among Jobs on one Objective."""

    @abstractmethod
    def send(
        self,
        *,
        objective_id: str,
        from_job_id: str,
        to_job_id: str,
        type: MessageType,
        body: str,
        artifact_refs: list[str] | None = None,
    ) -> AgentMessage: ...

    @abstractmethod
    def inbox(
        self, job_id: str, *, limit: int = 20, consume: bool = True
    ) -> list[AgentMessage]: ...

    @abstractmethod
    def list_peers(self, objective_id: str, *, exclude_job_id: str = "") -> list[dict]:
        """Return peer job summaries for an objective."""
