"""CrewAI tool adapters over capability bodies / job bridge."""

from __future__ import annotations

from orchestrator.agent.job import get_job
from orchestrator.capabilities.tools import (
    provision_cli_body,
    run_cli_body,
    sandbox_status_text,
)
from orchestrator.crew.tools.catalog import crew_tool


@crew_tool("sandbox_status", "Show bound sandbox mode and name.")
def sandbox_status() -> str:
    return sandbox_status_text()


@crew_tool(
    "provision_cli",
    "Install/verify a CLI on PATH via tools/catalog cascade. Prefer over apt/curl.",
)
def provision_cli(binary: str, package: str = "") -> str:
    return provision_cli_body(
        binary, package=package, enforce_role_allowlist=True
    )


@crew_tool(
    "run_cli",
    "Run an ad-hoc shell command inside the project sandbox (RoE applies).",
)
def run_cli(command: str) -> str:
    return run_cli_body(command)


@crew_tool("roe_status", "Show Rules of Engagement summary for this project.")
def roe_status() -> str:
    return get_job().bridge.roe_summary()


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
    "Record one subject discovery (host/port/service/vuln/asset) with evidence. "
    "Required: title. Prefer host, port, kind, evidence_path. "
    "Do not record objective/job/agent status — use update_objective_status.",
)
def record_finding(
    title: str,
    summary: str = "",
    severity: str = "info",
    kind: str = "",
    host: str = "",
    port: str = "",
    evidence_path: str = "",
) -> str:
    fields: dict = {
        "title": title,
        "description": summary,
        "summary": summary,
        "severity": severity,
        "kind": kind,
        "host": host,
        "evidence_path": evidence_path,
    }
    if str(port).strip():
        fields["port"] = port
    return get_job().bridge.record_finding(**fields)


@crew_tool(
    "record_findings",
    "Record multiple subject discoveries from a JSON list of finding objects. "
    "Each object needs title; prefer host, port, kind, evidence_path. "
    "Use when one artifact yields many inventory rows.",
)
def record_findings(findings_json: str) -> str:
    return get_job().bridge.record_findings(findings_json or "[]")


@crew_tool(
    "list_findings",
    "List recorded findings for this project (optional kind filter).",
)
def list_findings(kind: str = "") -> str:
    return get_job().bridge.list_findings(kind=kind)


@crew_tool(
    "list_workspace_artifacts",
    "List evidence files prior agents wrote under workspace/ (and findings/). "
    "Use before synthesizing a report — do not re-run scans to rediscover files.",
)
def list_workspace_artifacts() -> str:
    return get_job().bridge.list_workspace_artifacts()


@crew_tool(
    "read_workspace_artifact",
    "Read one evidence file by relative path (workspace/… or findings/…). "
    "Prefer this over shell cat for report synthesis.",
)
def read_workspace_artifact(path: str, max_chars: int = 100000) -> str:
    return get_job().bridge.read_workspace_artifact(path, max_chars=int(max_chars))


@crew_tool(
    "write_report_note",
    "Append a markdown section to findings/report.md. Analyzer-only; "
    "does not create a Finding row.",
)
def write_report_note(section: str, body: str) -> str:
    return get_job().bridge.write_report_note(section, body)


@crew_tool(
    "list_objectives",
    "List persisted project objectives (seq, status, title, role).",
)
def list_objectives() -> str:
    return get_job().bridge.list_objectives()


@crew_tool(
    "update_objective_status",
    "Update an objective status (pending/in_progress/completed/blocked/cancelled). "
    "Use for scheduler progress — never record this as a finding.",
)
def update_objective_status(seq: int, status: str, note: str = "") -> str:
    return get_job().bridge.update_objective_status(int(seq), status, note)
