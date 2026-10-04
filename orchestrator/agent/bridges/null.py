"""Fail-closed AgentBridgeBase for tests / unbound runs."""

from __future__ import annotations

from typing import Any

from orchestrator.agent.bridge_base import AgentBridgeBase, AgentLink


class NullAgentBridge(AgentBridgeBase):
    def emit(
        self, message_type: str, content: str, *, metadata: dict[str, Any] | None = None
    ) -> None:
        del message_type, content, metadata

    def spawn_agent(
        self,
        *,
        title: str,
        description: str,
        role_ids: list[str] | None = None,
        link: AgentLink = "peer",
    ) -> str:
        del title, description, role_ids, link
        raise RuntimeError("spawn_agent bridge not configured")

    def wait_agents(
        self, *, job_ids: list[str] | None = None, timeout_seconds: int = 600
    ) -> str:
        del job_ids, timeout_seconds
        raise RuntimeError("wait_agents bridge not configured")
