"""Sandbox tools for CrewAI roles (Session.exec only — never host shell)."""

from __future__ import annotations

from agent_runtime.api import Session
from orchestrator.crew.tools.decorators import crew_tool
from orchestrator.sandbox.shell import ShellRunner


def _bound() -> str | None:
    return Session.require_bound()


def _emit(kind: str, content: str, **metadata: object) -> None:
    from orchestrator.agent.job import get_job

    get_job().bridge.emit(kind, content, metadata=dict(metadata) if metadata else None)


@crew_tool("sandbox_status", "Show bound sandbox mode and name.")
def sandbox_status() -> str:
    err = _bound()
    if err:
        return err
    info = Session.current().info
    return f"name={info.name} mode={info.mode} session_id={info.project_id or '-'}"


@crew_tool(
    "provision_cli",
    "Install/verify a CLI on PATH via tools/catalog cascade. Prefer over apt/curl.",
)
def provision_cli(binary: str, package: str = "") -> str:
    err = _bound()
    if err:
        return err
    name = (binary or "").strip()
    if not name:
        return "Error: provide a binary name."
    from orchestrator.agent.job import get_job
    from orchestrator.crew.roles.registry import RoleRegistry
    from orchestrator.tools.install import InstallResolver

    role_id = str(get_job().extras.get("role_id") or "").strip()
    if role_id:
        role = RoleRegistry.shared().get(role_id)
        if role and role.allow_binaries and name not in role.allow_binaries:
            return (
                f"Error: binary {name!r} not allowlisted for role {role_id}. "
                f"Allowed: {', '.join(role.allow_binaries)}"
            )
    ok, msg = InstallResolver.shared().resolve(name, package=package, skill_name="")
    if ok:
        _emit("log", msg or f"provisioned {name}")
        return msg or f"provisioned {name}"
    return f"Error: {msg}"


@crew_tool(
    "run_cli",
    "Run an ad-hoc shell command inside the project sandbox (RoE applies).",
)
def run_cli(command: str) -> str:
    err = _bound()
    if err:
        return err
    cmd = (command or "").strip()
    if not cmd:
        return "Error: empty command."
    from orchestrator.agent.job import get_job

    bridge = get_job().bridge
    gate = bridge.assert_command_allowed(cmd)
    if gate:
        return f"Error: RoE blocked — {gate}"
    _emit("tool", f"run_cli: {cmd[:200]}")
    return f"exit={ShellRunner.shared().run_shell(cmd)}"
