"""LangChain capability tools for the sandbox agent.

Importing this module registers tools via ``@capability`` decorators.
Call ``orchestrator.capabilities.ensure_registered()`` so registration runs
even when this module was not imported yet.

Shared bodies (``sandbox_status_text``, ``provision_cli_body``, ``run_cli_body``,
stream helpers) are plain functions so CrewAI adapters can call the same code
without a third wrapper module.
"""

from __future__ import annotations

from typing import Any, Mapping

from langchain_core.tools import tool

from agent_runtime.api import Session
from orchestrator.agent.job import get_agent_config, get_job
from orchestrator.capabilities.registry import CapabilityGroup, capability
from orchestrator.crew.roles.registry import RoleRegistry
from orchestrator.sandbox.shell import ShellRunner
from orchestrator.tools.install import InstallResolver
from orchestrator.utils.stream_events import (
    EVENT_PROVISION_CLI,
    EVENT_RUN_CLI,
    EVENT_RUN_PERIODIC,
    EVENT_SPAWN_AGENT,
    cli_payload,
    envelope,
)


# --- shared bodies (also used by orchestrator.crew.tools.adapters) -------------


def emit(kind: str, content: str, **metadata: object) -> None:
    get_job().bridge.emit(kind, content, metadata=dict(metadata) if metadata else None)


def emit_event(
    kind: str,
    content: str,
    event: str,
    payload: Mapping[str, Any] | None = None,
    **meta: object,
) -> None:
    emit(kind, content, **envelope(event, payload, **meta))


def require_bound() -> str | None:
    return Session.require_bound()


def sandbox_status_text(*, extra_binaries: bool = False) -> str:
    err = require_bound()
    if err:
        return err
    info = Session.current().info
    lines = [
        f"name={info.name} mode={info.mode} session_id={info.project_id or '-'}"
    ]
    if extra_binaries:
        try:
            which = Session.current().which
            for binary in ("python3", "bash", "curl", "apt-get"):
                lines.append(f"{binary}={'yes' if which(binary) else 'no'}")
        except Exception:
            pass
    return "\n".join(lines)


def provision_cli_body(
    binary: str,
    package: str = "",
    *,
    enforce_role_allowlist: bool = False,
) -> str:
    err = require_bound()
    if err:
        return err
    name = (binary or "").strip()
    if not name:
        return "Error: provide a binary name."

    if enforce_role_allowlist:
        role_id = str(get_job().extras.get("role_id") or "").strip()
        if role_id:
            role = RoleRegistry.shared().get(role_id)
            if role and role.allow_binaries and name not in role.allow_binaries:
                return (
                    f"Error: binary {name!r} not allowlisted for role {role_id}. "
                    f"Allowed: {', '.join(role.allow_binaries)}"
                )

    ok, msg = InstallResolver.shared().resolve(name, package=package)
    if ok:
        text = msg or f"provisioned {name}"
        emit_event(
            "log",
            text,
            EVENT_PROVISION_CLI,
            {"binary": name, "package": package or ""},
        )
        return text
    return f"Error: {msg}"


def run_cli_body(command: str) -> str:
    """Run a sandbox shell command with RoE gating via the job bridge."""
    err = require_bound()
    if err:
        return err
    cmd = (command or "").strip()
    if not cmd:
        return "Error: empty command."
    bridge = get_job().bridge
    gate = bridge.assert_command_allowed(cmd)
    if gate:
        return f"Error: RoE blocked — {gate}"
    dup = ""
    checker = getattr(bridge, "duplicate_scan_reason", None)
    if callable(checker):
        dup = checker(cmd) or ""
    if dup:
        return f"Skipped: {dup}"
    payload = cli_payload(cmd)
    emit_event("tool", f"run_cli: {cmd[:200]}", EVENT_RUN_CLI, payload)
    return f"exit={ShellRunner.shared().run_shell(cmd)}"


# --- LangChain registrations --------------------------------------------------


@capability(CapabilityGroup.SANDBOX)
@tool
def sandbox_setup() -> str:
    """Confirm the Docker sandbox is bound (host provisions before the agent)."""
    err = require_bound()
    if err:
        return err
    return "Sandbox ready — " + sandbox_status_text()


@capability(CapabilityGroup.SANDBOX)
@tool
def sandbox_status() -> str:
    """Show bound sandbox mode and name."""
    return sandbox_status_text(extra_binaries=True)


@capability(CapabilityGroup.SANDBOX)
@tool
def provision_cli(binary: str, package: str = "") -> str:
    """Install/verify a CLI on PATH (tools/catalog → LLM). Prefer over apt/curl via run_cli."""
    return provision_cli_body(binary, package=package)


@capability(CapabilityGroup.SANDBOX)
@tool
def run_cli(command: str) -> str:
    """Ad-hoc sandbox shell (RoE applies). Not for installs — use provision_cli."""
    return run_cli_body(command)


@capability(CapabilityGroup.SANDBOX, tags={"watchdog", "periodic"})
@tool
def run_periodic(
    command: str,
    interval_seconds: int = 30,
    duration_seconds: int = 120,
    package: str = "",
) -> str:
    """Watchdog ticks only — repeat a sandbox shell command on an interval."""
    err = require_bound()
    if err:
        return err
    cmd = (command or "").strip()
    if not cmd:
        return "Error: empty command."
    emit_event(
        "tool",
        f"run_periodic({interval_seconds}s/{duration_seconds}s): {cmd[:160]}",
        EVENT_RUN_PERIODIC,
        cli_payload(
            cmd,
            interval_seconds=int(interval_seconds),
            duration_seconds=int(duration_seconds),
            package=package or "",
        ),
    )
    code = ShellRunner.shared().run_periodic(
        cmd,
        interval_seconds=int(interval_seconds),
        duration_seconds=int(duration_seconds),
        package=package or "",
    )
    return f"exit={code}"


@capability(CapabilityGroup.ENGAGEMENT)
@tool
def list_objectives() -> str:
    """List host-defined objectives for the current session (via ports)."""
    return get_job().bridge.list_objectives()


@capability(CapabilityGroup.ENGAGEMENT)
@tool
def update_objective_status(seq: int, status: str, note: str = "") -> str:
    """Update an objective status via host ports."""
    return get_job().bridge.update_objective_status(int(seq), status, note)


@capability(CapabilityGroup.ENGAGEMENT)
@tool
def record_finding(
    title: str,
    severity: str = "info",
    kind: str = "observation",
    evidence: str = "",
    host: str = "",
    description: str = "",
    asset_type: str = "",
    evidence_path: str = "",
    remediation: str = "",
) -> str:
    """Record one engagement finding about a subject — not run/objective status."""
    return get_job().bridge.record_finding(
        title=title,
        severity=severity,
        kind=kind,
        evidence=evidence,
        host=host,
        description=description,
        asset_type=asset_type,
        evidence_path=evidence_path,
        remediation=remediation,
    )


@capability(CapabilityGroup.ENGAGEMENT)
@tool
def record_findings(findings_json: str) -> str:
    """Record multiple engagement findings from a JSON list (not status updates)."""
    return get_job().bridge.record_findings(findings_json)


@capability(CapabilityGroup.ENGAGEMENT)
@tool
def list_findings(kind: str = "") -> str:
    """List engagement findings already recorded (optional kind slug filter)."""
    return get_job().bridge.list_findings(kind=kind)


@capability(CapabilityGroup.ENGAGEMENT, tags={"multiagent"})
@tool
def spawn_agent(
    title: str,
    description: str,
    role_id: str = "",
    link: str = "peer",
) -> str:
    """Spawn another Job agent.

    link=peer (default): same objective, focused brief — preferred multi-agent path.
    link=child: subordinate of this job (depth-limited).
    role_id: optional primary role id (defaults to this job's role on the host).
    """
    cfg = get_agent_config()
    ctx = get_job()
    kind = (link or "peer").strip().lower()
    if kind not in {"peer", "child"}:
        return "Error: link must be 'peer' or 'child'"
    if kind == "child" and ctx.depth >= cfg.max_subagent_depth:
        return f"Error: child depth limit ({cfg.max_subagent_depth})"
    names = [role_id.strip()] if (role_id or "").strip() else None
    try:
        job_id = ctx.bridge.spawn_agent(
            title=title,
            description=description,
            role_ids=names,
            link=kind,  # type: ignore[arg-type]
        )
    except Exception as exc:
        return f"Error spawning agent: {exc}"
    emit_event(
        "log",
        f"spawned {kind} agent {job_id}: {title}",
        EVENT_SPAWN_AGENT,
        {"link": kind, "job_id": job_id, "title": title},
    )
    return job_id if job_id.startswith("Error") else f"spawned {kind} job {job_id}"


@capability(CapabilityGroup.ENGAGEMENT, tags={"multiagent"})
@tool
def wait_for_agents(timeout_seconds: int = 600, job_ids: str = "") -> str:
    """Non-blocking status of child agents this job spawned (call again later)."""
    ids = [j.strip() for j in (job_ids or "").split(",") if j.strip()] or None
    try:
        return get_job().bridge.wait_agents(
            job_ids=ids, timeout_seconds=int(timeout_seconds)
        )
    except Exception as exc:
        return f"Error waiting for agents: {exc}"


@capability(CapabilityGroup.ENGAGEMENT, tags={"multiagent"})
@tool
def list_agents() -> str:
    """List other Jobs on the same objective."""
    return get_job().bridge.list_agents()


@capability(CapabilityGroup.ENGAGEMENT, tags={"multiagent"})
@tool
def send_agent_message(
    body: str,
    to_job_id: str = "",
    type: str = "inform",
    artifact_refs: str = "",
) -> str:
    """Send a message to another Job on this objective (broadcast if to_job_id empty)."""
    refs = [p.strip() for p in (artifact_refs or "").split(",") if p.strip()]
    return get_job().bridge.send_message(
        to_job_id=to_job_id or "",
        type=(type or "inform").strip().lower(),
        body=body or "",
        artifact_refs=refs,
    )


@capability(CapabilityGroup.ENGAGEMENT, tags={"multiagent"})
@tool
def propose_agents(context_notes: str = "", max_agents: int = 4) -> str:
    """Deduce and spawn peer agents from objective context (LLM)."""
    return get_job().bridge.propose_agents(
        context_notes=context_notes or "", max_agents=int(max_agents or 4)
    )
