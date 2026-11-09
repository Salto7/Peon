"""Control-plane bridge ABC for one Job agent.

Peon implements this; the agent calls into it for streaming, spawn, findings, etc.
Not the Docker/OpenShell runtime — that is ``agent_runtime``.
This module does not import peon. Other orchestrator modules do.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal

AgentLink = Literal["peer", "child"]


class AgentBridgeBase(ABC):
    """Control-plane adapter injected per Job."""

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

    @abstractmethod
    def list_agents(self) -> str: ...

    @abstractmethod
    def list_delegations(self) -> str:
        """Spawn tree for manager oversight (project/objective)."""

    @abstractmethod
    def propose_agents(self, *, context_notes: str = "", max_agents: int = 4) -> str:
        """Propose (and optionally spawn) context-driven Jobs."""

    @abstractmethod
    def append_transcript(self, record: Any) -> None:
        """Persist a structured transcript record."""

    @abstractmethod
    def list_objectives(self) -> str: ...

    @abstractmethod
    def update_objective_status(self, seq: int, status: str, note: str = "") -> str: ...

    @abstractmethod
    def ask_operator(
        self,
        question: str,
        *,
        options: str = "",
        blocks_job: bool = True,
    ) -> str:
        """Request human-in-the-loop input (typed OperatorPrompt)."""

    @abstractmethod
    def record_finding(self, **fields: Any) -> str: ...

    @abstractmethod
    def record_findings(self, findings_json: str) -> str: ...

    @abstractmethod
    def list_findings(self, kind: str = "") -> str: ...

    # --- Agent-to-agent (A2A) messaging --------------------------------

    #: Set by ``go_idle``; the control plane parks the job after its turn ends.
    idle_requested: bool = False
    idle_note: str = ""

    @abstractmethod
    def send_message(
        self, to: str, content: str, *, kind: str = "inform", reply_to: str = ""
    ) -> str:
        """Send an A2A message to one or more live agents (wakes/resumes as needed)."""

    @abstractmethod
    def check_inbox(self) -> str:
        """Read (and consume) messages other agents or the operator sent."""

    @abstractmethod
    def message_status(self, limit: int = 10) -> str:
        """Delivery/read state of messages this agent sent, most recent first."""

    @abstractmethod
    def go_idle(self, reason: str = "") -> str:
        """Park this agent until another agent/operator messages it."""

    @abstractmethod
    def list_team(self) -> str:
        """Every agent on the project with id, objective, role and state."""

    # --- Memory --------------------------------------------------------

    @abstractmethod
    def recall_memory(self, query: str) -> str:
        """Search persistent memory (this engagement + platform lessons)."""

    @abstractmethod
    def save_memory(
        self,
        name: str,
        kind: str,
        description: str,
        body: str = "",
        scope: str = "project",
    ) -> str:
        """Create/update a durable memory entry."""

    @abstractmethod
    def forget_memory(self, name: str, scope: str = "project") -> str:
        """Retire a stale memory entry."""

    # --- Credentials (project key store) --------------------------------

    @abstractmethod
    def list_credentials(self) -> str:
        """List credential names/kinds stored for this project (never values)."""

    @abstractmethod
    def get_credential(self, name: str, part: str = "secret") -> str:
        """Plaintext of a stored credential (``secret``|``username``|``domain``|``full``)."""

    @abstractmethod
    def record_assets(self, assets_json: str, relations_json: str = "") -> str:
        """Add assets/relations to the engagement asset graph (Open Asset Model)."""

    @abstractmethod
    def write_report_note(self, section: str, body: str) -> str:
        """Append a markdown section into findings/report.md (not a Finding row)."""

    @abstractmethod
    def write_workspace_artifact(self, path: str, content: str) -> str:
        """Write a text deliverable under ``workspace/`` (no execution)."""

    @abstractmethod
    def list_workspace_artifacts(self) -> str:
        """List evidence files under the job workspace (prior agent outputs)."""

    @abstractmethod
    def read_workspace_artifact(self, path: str, max_chars: int = 100_000) -> str:
        """Read one workspace/ or findings/ evidence file."""

    @abstractmethod
    def drain_operator_guidance(self) -> list[str]: ...

    # --- RoE (CrewAI roles; peon implements against RulesOfEngagement) ---

    @abstractmethod
    def roe_summary(self) -> str: ...

    @abstractmethod
    def roe_probe_block_reason(self) -> str:
        """Empty when RoE in_scope is usable for probes; else denial reason."""

    @abstractmethod
    def assert_in_scope(self, target: str) -> str:
        """Return empty string if allowed, else a denial reason."""

    @abstractmethod
    def assert_command_allowed(self, command: str) -> str:
        """Return empty string if sandbox command may run, else denial reason."""

    @abstractmethod
    def search_evidence(
        self, kind: str = "", host: str = "", finding_seq: int = 0
    ) -> str:
        """Evidence anywhere in this project, not only this job."""

    @abstractmethod
    def record_observation(self, asset_key: str, text: str) -> str:
        """Note something about an asset-graph node."""

    @abstractmethod
    def record_intent(self, asset_key: str, text: str, status: str = "planned") -> str:
        """Plan something about an asset-graph node."""

    @abstractmethod
    def request_phase_advance(self, required_phase: str) -> None:
        """Ask the operator to advance the objective phase."""

    @abstractmethod
    def request_spawn_cap_approval(self, reason: str) -> None:
        """Notify the operator that a spawn hit the fan-out cap."""

    @abstractmethod
    def current_phase(self) -> str:
        """This job's objective phase, or empty when there is none."""

    @abstractmethod
    def duplicate_scan_reason(self, command: str) -> str:
        """Non-empty when this job already ran the same catalog CLI against the host."""

    @abstractmethod
    def stagnation_note(self, command: str) -> str:
        """Non-empty nudge when catalog probes are not producing findings."""
