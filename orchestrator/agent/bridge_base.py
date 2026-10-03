"""Control-plane bridge ABC for one Job agent (orchestrator never imports peon).

Peon implements this; the agent calls into it for streaming, spawn, findings, etc.
Not the Docker/OpenShell runtime — that is ``agent_runtime``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal

AgentLink = Literal["peer", "child"]


class AgentBridgeBase(ABC):
    """Control-plane adapter injected per Job so the library stays peon-agnostic."""

    @abstractmethod
    def emit(
        self, message_type: str, content: str, *, metadata: dict[str, Any] | None = None
    ) -> None: ...

    @abstractmethod
    def spawn_agent(
        self,
        *,
        title: str,
        description: str,
        skill_names: list[str] | None = None,
        link: AgentLink = "peer",
    ) -> str:
        """Enqueue another Job.

        ``peer`` — same objective, focused brief (typical multi-agent).
        ``child`` — subordinate of this job (depth-limited tree).
        """

    @abstractmethod
    def wait_agents(
        self, *, job_ids: list[str] | None = None, timeout_seconds: int = 600
    ) -> str:
        """Non-blocking status of agents this job spawned (children)."""

    def list_agents(self) -> str:
        return "list_agents bridge not configured."

    def propose_agents(self, *, context_notes: str = "", max_agents: int = 4) -> str:
        """Propose (and optionally spawn) context-driven peer Jobs."""
        del context_notes, max_agents
        return "propose_agents bridge not configured."

    def list_objectives(self) -> str:
        return "Objectives bridge not configured."

    def update_objective_status(self, seq: int, status: str, note: str = "") -> str:
        del seq, status, note
        return "update_objective_status bridge not configured."

    def record_finding(self, **fields: Any) -> str:
        del fields
        return "record_finding bridge not configured."

    def record_findings(self, findings_json: str) -> str:
        del findings_json
        return "record_findings bridge not configured."

    def list_findings(self, kind: str = "") -> str:
        del kind
        return "list_findings bridge not configured."

    def drain_operator_guidance(self) -> list[str]:
        return []

    def drain_peer_messages(self) -> list[str]:
        return []

    def send_message(
        self,
        *,
        to_job_id: str,
        type: str,
        body: str,
        artifact_refs: list[str] | None = None,
    ) -> str:
        del to_job_id, type, body, artifact_refs
        return "send_message bridge not configured."
