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
        role_ids: list[str] | None = None,
        link: AgentLink = "child",
        scope_slice: dict[str, str] | None = None,
        skill_ids: list[str] | None = None,
    ) -> str:
        """Enqueue another Job.

        ``child`` (default) — subordinate of this job (depth-limited tree).
        ``peer`` — same objective, focused brief.
        Empty ``role_ids`` inherits this job's role (same-role fan-out).
        Optional ``skill_ids`` selects a subset of the target role's skill group;
        empty inherits parent/objective skills (or the full role group).
        """

    @abstractmethod
    def wait_agents(
        self, *, job_ids: list[str] | None = None, timeout_seconds: int = 600
    ) -> str:
        """Non-blocking status of agents this job spawned (children)."""

    def list_agents(self) -> str:
        return "list_agents bridge not configured."

    def list_delegations(self) -> str:
        """Spawn tree for manager oversight (project/objective)."""
        return "list_delegations bridge not configured."

    def propose_agents(self, *, context_notes: str = "", max_agents: int = 4) -> str:
        """Propose (and optionally spawn) context-driven Jobs."""
        del context_notes, max_agents
        return "propose_agents bridge not configured."

    def append_transcript(self, record: Any) -> None:
        """Persist a structured transcript record (optional)."""
        del record
        return None

    def list_objectives(self) -> str:
        return "Objectives bridge not configured."

    def update_objective_status(self, seq: int, status: str, note: str = "") -> str:
        del seq, status, note
        return "update_objective_status bridge not configured."

    def ask_operator(
        self,
        question: str,
        *,
        options: str = "",
        blocks_job: bool = True,
    ) -> str:
        """Request human-in-the-loop input (typed OperatorPrompt)."""
        del question, options, blocks_job
        return "ask_operator bridge not configured."

    def record_finding(self, **fields: Any) -> str:
        del fields
        return "record_finding bridge not configured."

    def record_findings(self, findings_json: str) -> str:
        del findings_json
        return "record_findings bridge not configured."

    def list_findings(self, kind: str = "") -> str:
        del kind
        return "list_findings bridge not configured."

    def write_report_note(self, section: str, body: str) -> str:
        """Append a markdown section into findings/report.md (not a Finding row)."""
        del section, body
        return "write_report_note bridge not configured."

    def list_workspace_artifacts(self) -> str:
        """List evidence files under the job workspace (prior agent outputs)."""
        return "list_workspace_artifacts bridge not configured."

    def read_workspace_artifact(self, path: str, max_chars: int = 100_000) -> str:
        """Read one workspace/ or findings/ evidence file."""
        del path, max_chars
        return "read_workspace_artifact bridge not configured."

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

    # --- RoE (CrewAI roles; peon implements against RulesOfEngagement) ---

    def roe_summary(self) -> str:
        return "RoE bridge not configured."

    def roe_probe_block_reason(self) -> str:
        """Empty when RoE in_scope is usable for probes; else denial reason."""
        return "RoE bridge not configured."

    def assert_in_scope(self, target: str) -> str:
        """Return empty string if allowed, else a denial reason."""
        del target
        return "RoE bridge not configured."

    def assert_command_allowed(self, command: str) -> str:
        """Return empty string if sandbox command may run, else denial reason."""
        del command
        return ""

