"""CrewAI tools. Command tools call ``SandboxTools``; engagement tools use the job bridge."""

from __future__ import annotations

import json
from typing import Any, Callable

from orchestrator.agent.job import get_job
from orchestrator.agent.delegation import (
    list_delegations_body,
    list_roles_body,
    list_work_body,
    propose_work_body,
    send_agent_message_body,
    spawn_work_body,
    wait_for_work_body,
)
from orchestrator.capabilities.engagement import EngagementTools
from orchestrator.crew.tools.catalog import crew_tool
from orchestrator.skills.runtime.bodies import (
    load_skill_body,
    read_skill_resource_body,
    run_skill_script_body,
)
from orchestrator.skills.runtime.sandbox import SandboxTools


def _bridge(method: str) -> Callable[..., str]:
    def call(*args: Any, **kwargs: Any) -> str:
        return getattr(get_job().bridge, method)(*args, **kwargs)

    return call


@crew_tool("sandbox_status", "Show bound sandbox mode and name.")
def sandbox_status() -> str:
    return SandboxTools().status()


@crew_tool(
    "provision_cli",
    "Install/verify a CLI on PATH via tools/catalog cascade. Prefer over apt/curl.",
)
def provision_cli(binary: str, package: str = "") -> str:
    return SandboxTools().provision(
        binary, package=package, enforce_role_allowlist=True
    )


@crew_tool(
    "bash",
    "Claude Bash tool. Run a shell command in this job's project sandbox "
    "(RoE applies). Nothing runs on the host.",
)
def bash(command: str) -> str:
    return SandboxTools().bash(command)


@crew_tool(
    "run_periodic",
    "Repeat a sandbox shell command on an interval (watchdog ticks). RoE applies.",
)
def run_periodic(
    command: str,
    interval_seconds: int = 30,
    duration_seconds: int = 120,
    package: str = "",
) -> str:
    return SandboxTools().periodic(
        command,
        interval_seconds=interval_seconds,
        duration_seconds=duration_seconds,
        package=package,
    )


@crew_tool("roe_status", "Show Rules of Engagement summary for this project.")
def roe_status() -> str:
    return _bridge("roe_summary")()


@crew_tool(
    "assert_in_scope",
    "Check whether a target value is authorized under active RoE before probing.",
)
def assert_in_scope(target: str) -> str:
    value = (target or "").strip()
    if not value:
        return "Error: empty target."
    reason = get_job().bridge.assert_in_scope(value)
    if reason:
        return f"DENIED: {reason}"
    return f"ALLOWED: {value} is in-scope under current RoE."


@crew_tool(
    "record_finding",
    "Record one engagement subject discovery with optional evidence provenance. "
    "Required: title plus at least one subject (host/port or subjects JSON). "
    "evidence_path is workspace provenance only — never a graph subject. "
    "Do not record objective/job/agent status — use update_objective_status.",
)
def record_finding(
    title: str,
    summary: str = "",
    severity: str = "info",
    kind: str = "",
    host: str = "",
    port: str = "",
    protocol: str = "",
    evidence_path: str = "",
    subjects_json: str = "",
) -> str:
    fields: dict = {
        "title": title,
        "description": summary,
        "summary": summary,
        "severity": severity,
        "kind": kind,
        "host": host,
        "protocol": protocol,
        "evidence_path": evidence_path,
    }
    if str(port).strip():
        fields["port"] = port
    raw_subjects = (subjects_json or "").strip()
    if raw_subjects:
        try:
            parsed = json.loads(raw_subjects)
        except json.JSONDecodeError as exc:
            return f"Error: subjects_json must be JSON list: {exc}"
        if not isinstance(parsed, list):
            return "Error: subjects_json must be a JSON list."
        fields["subjects"] = parsed
    return EngagementTools.record_finding(**fields)


@crew_tool(
    "record_findings",
    "Record multiple subject discoveries from a JSON list. Each object needs "
    "title and at least one subject (host/port or subjects:[{type,value}]). "
    "evidence_path is provenance only. Use when one artifact yields many rows.",
)
def record_findings(findings_json: str) -> str:
    return EngagementTools.record_findings(findings_json or "[]")


@crew_tool(
    "list_findings",
    "List recorded findings for this project (optional kind filter).",
)
def list_findings(kind: str = "") -> str:
    return EngagementTools.list_findings(kind=kind)


@crew_tool(
    "list_workspace_artifacts",
    "List evidence files prior agents wrote under workspace/ (and findings/). "
    "Use before synthesizing a report — do not re-run scans to rediscover files.",
)
def list_workspace_artifacts() -> str:
    return _bridge("list_workspace_artifacts")()


@crew_tool(
    "read_workspace_artifact",
    "Read one evidence file by relative path (workspace/… or findings/…). "
    "Prefer this over shell cat for report synthesis.",
)
def read_workspace_artifact(path: str, max_chars: int = 100000) -> str:
    return _bridge("read_workspace_artifact")(path, max_chars=int(max_chars))


@crew_tool(
    "write_report_note",
    "Append a markdown section to findings/report.md. Analyzer-only; "
    "does not create a Finding row.",
)
def write_report_note(section: str, body: str) -> str:
    return _bridge("write_report_note")(section, body)


@crew_tool(
    "list_objectives",
    "List persisted project objectives (seq, status, title, role).",
)
def list_objectives() -> str:
    return EngagementTools.list_objectives()


@crew_tool(
    "update_objective_status",
    "Update an objective status (pending/in_progress/completed/blocked/cancelled). "
    "Use for scheduler progress — never record this as a finding.",
)
def update_objective_status(seq: int, status: str, note: str = "") -> str:
    return EngagementTools.update_objective_status(int(seq), status, note)


# --- multi-agent / collaboration (auto-injected for engagement roles) ---------


@crew_tool(
    "list_roles",
    "Browse the live CrewAI role catalog (id, goal, capabilities). "
    "Use before spawn_work to pick an existing role_id (or omit role_id to "
    "spawn the same role as this job).",
)
def list_roles() -> str:
    return list_roles_body()


@crew_tool(
    "spawn_work",
    "Spawn a child Job (default) or peer Job for in-lane follow-on skills or when "
    "the operator/brief asks for parallel work. Empty role_id = same expert. "
    "Optional skill_ids (comma-separated subset of this role's skill group). "
    "link=child|peer. Optional scope_slice_json under RoE — do not invent smaller "
    "CIDR slices of an authorized netblock unless the operator asked. After "
    "spawning, poll wait_for_work until children finish. Do not replan via PM "
    "for work already in your skill group.",
)
def spawn_work(
    title: str,
    description: str,
    role_id: str = "",
    link: str = "child",
    scope_slice_json: str = "",
    skill_ids: str = "",
) -> str:
    return spawn_work_body(
        title,
        description,
        role_id=role_id,
        link=link,
        scope_slice_json=scope_slice_json,
        skill_ids=skill_ids,
    )


@crew_tool(
    "wait_for_work",
    "Non-blocking status of child jobs this agent spawned. After spawn_work, call "
    "again later until all children are terminal; do not busy-wait in a tight loop.",
)
def wait_for_work(timeout_seconds: int = 600, job_ids: str = "") -> str:
    return wait_for_work_body(
        timeout_seconds=int(timeout_seconds), job_ids=job_ids or ""
    )


@crew_tool(
    "list_work",
    "List other Jobs on the same objective (peers / siblings).",
)
def list_work() -> str:
    return list_work_body()


@crew_tool(
    "list_delegations",
    "Show the spawn/delegation tree for this project or objective "
    "(manager oversight).",
)
def list_delegations() -> str:
    return list_delegations_body()


@crew_tool(
    "propose_work",
    "LLM-propose focused child/peer Jobs from objective context + transcript; "
    "spawns only existing catalog role ids.",
)
def propose_work(context_notes: str = "", max_agents: int = 4) -> str:
    return propose_work_body(
        context_notes=context_notes or "", max_agents=int(max_agents or 4)
    )


@crew_tool(
    "send_agent_message",
    "Send a message to another Job on this objective "
    "(broadcast if to_job_id empty).",
)
def send_agent_message(
    body: str,
    to_job_id: str = "",
    type: str = "inform",
    artifact_refs: str = "",
) -> str:
    return send_agent_message_body(
        body,
        to_job_id=to_job_id,
        type=type,
        artifact_refs=artifact_refs,
    )


@crew_tool(
    "ask_operator",
    "Ask the human operator a question (HITL popup). Use when RoE is ambiguous, "
    "replans are stuck, or a decision is required. options = comma-separated "
    "choices. blocks_job=true also blocks the current objective until reply.",
)
def ask_operator(
    question: str,
    options: str = "",
    blocks_job: bool = True,
) -> str:
    return EngagementTools.ask_operator(
        question,
        options=options or "",
        blocks_job=bool(blocks_job),
    )


@crew_tool(
    "load_skill",
    "Load the full instructions for a progressive skill (claude/<name> or name).",
)
def load_skill(name: str) -> str:
    return load_skill_body(name)


@crew_tool(
    "read_skill_resource",
    "Read a file inside a skill pack. path is relative to the skill root.",
)
def read_skill_resource(name: str, path: str) -> str:
    return read_skill_resource_body(name, path)


@crew_tool(
    "run_skill_script",
    "Run a skill script in this job's project sandbox. "
    "args is a JSON list of strings. "
    "cwd is the writable workspace; $SKILL_DIR is the read-only pack root; "
    "artifacts belong in the project workspace.",
)
def run_skill_script(
    name: str,
    relative_path: str,
    args: str = "",
) -> str:
    return run_skill_script_body(name, relative_path, args)
