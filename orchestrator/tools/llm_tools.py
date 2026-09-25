"""LangChain capability tools for the sandbox agent.

Importing this module registers tools via ``@capability`` decorators.
Call ``orchestrator.capabilities.ensure_registered()`` so registration runs
even when this module was not imported yet.
"""

from __future__ import annotations

import os

from langchain_core.tools import tool

from orchestrator.agent.context import get_agent_config, get_context
from orchestrator.capabilities.registry import CapabilityGroup, capability
from orchestrator.runtime.shell import ShellRunner
from orchestrator.sandbox import SandboxSession
from orchestrator.skills.execute import SkillExecutionDispatcher, SkillRunRequest
from orchestrator.skills.misc.catalog import filter_skills
from orchestrator.skills.misc.registry import SkillRegistry


def _emit(kind: str, content: str, **metadata: object) -> None:
    get_context().ports.emit(kind, content, **metadata)


def _bound() -> str | None:
    """Return an error string if the sandbox is unbound, else None."""
    return SandboxSession.require_bound()


def _sandbox_line() -> str:
    info = SandboxSession.current().info
    return f"name={info.name} mode={info.mode} session_id={info.project_id or '-'}"


@capability(CapabilityGroup.SANDBOX)
@tool
def sandbox_setup() -> str:
    """Confirm the Docker sandbox is bound (host provisions before the agent)."""
    return _bound() or ("Sandbox ready — " + _sandbox_line())


@capability(CapabilityGroup.SANDBOX)
@tool
def sandbox_status() -> str:
    """Show bound sandbox mode and name."""
    err = _bound()
    if err:
        return err
    lines = [_sandbox_line()]
    try:
        which = SandboxSession.current().which
        for binary in ("python3", "bash", "curl", "apt-get"):
            lines.append(f"{binary}={'yes' if which(binary) else 'no'}")
    except Exception:
        pass
    return "\n".join(lines)


@capability(CapabilityGroup.SANDBOX)
@tool
def provision_cli(binary: str, package: str = "", skill_name: str = "") -> str:
    """Install/verify a CLI on PATH (tools/catalog → skill INSTALL.md → LLM). Prefer over apt/curl via run_cli."""
    err = _bound()
    if err:
        return err
    name = (binary or "").strip()
    if not name:
        return "Error: provide a binary name."
    from orchestrator.runtime.resolve import InstallResolver

    skill = (skill_name or "").strip() or (
        os.environ.get("ORCHESTRATOR_SKILL_NAME") or ""
    ).strip()
    ok, msg = InstallResolver.shared().resolve(
        name, package=package, skill_name=skill
    )
    if ok:
        _emit("log", msg or f"provisioned {name}")
        return msg or f"provisioned {name}"
    return f"Error: {msg}"


@capability(CapabilityGroup.SANDBOX)
@tool
def run_cli(command: str) -> str:
    """Ad-hoc shell when no skill script applies (RoE applies). Not for installs."""
    err = _bound()
    if err:
        return err
    cmd = (command or "").strip()
    if not cmd:
        return "Error: empty command."
    _emit("tool", f"run_cli: {cmd[:200]}")
    return f"exit={ShellRunner.shared().run_shell(cmd)}"


@capability(CapabilityGroup.SANDBOX, tags={"watchdog", "periodic"})
@tool
def run_periodic(
    command: str,
    interval_seconds: int = 30,
    duration_seconds: int = 120,
    package: str = "",
) -> str:
    """Watchdog ticks only — repeat a sandbox shell command on an interval."""
    err = _bound()
    if err:
        return err
    cmd = (command or "").strip()
    if not cmd:
        return "Error: empty command."
    _emit(
        "tool",
        f"run_periodic({interval_seconds}s/{duration_seconds}s): {cmd[:160]}",
    )
    code = ShellRunner.shared().run_periodic(
        cmd,
        interval_seconds=int(interval_seconds),
        duration_seconds=int(duration_seconds),
        package=package or "",
    )
    return f"exit={code}"


@capability(CapabilityGroup.SKILLS)
@tool
def run_skill_script(
    skill_name: str, script: str = "scripts/run.py", command: str = ""
) -> str:
    """Run a catalog skill script. Prefer this over rewriting the skill with run_cli."""
    err = _bound()
    if err:
        return err
    skill = (skill_name or "").strip()
    path = (script or "scripts/run.py").strip() or "scripts/run.py"
    if not skill:
        return "Error: skill_name required."
    _emit(
        "tool",
        f"run_skill_script({skill}, {path}"
        + (f", command={command[:120]!r}" if command else "")
        + ")",
    )
    result = SkillExecutionDispatcher.shared().run(
        SkillRunRequest(skill_name=skill, script=path, command=command or "")
    )
    out = (result.output or "").strip()
    if len(out) > 12000:
        out = out[:12000] + "\n…(truncated)"
    return f"ok={result.ok} exit={result.exit_code}\n{out}"


@capability(CapabilityGroup.SKILLS)
@tool
def skills_list() -> str:
    """List jobable skills from the filesystem catalog."""
    skills = filter_skills(
        SkillRegistry.shared().get_registry().values(), jobable_only=True
    )
    if not skills:
        return "No jobable skills."
    lines = []
    for s in sorted(skills, key=lambda x: x.name):
        lines.append(f"{s.name} [{s.category or '-'}] — {(s.description or '')[:120]}")
    return "\n".join(lines)


@capability(CapabilityGroup.SKILLS)
@tool
def skill_view(name: str, path: str = "") -> str:
    """Show a skill's instructions (or a reference file under the skill dir)."""
    skill = SkillRegistry.shared().load_skill((name or "").strip())
    if skill is None:
        return f"Skill not found: {name!r}"
    return skill.format_view(path=path)


@capability(CapabilityGroup.ENGAGEMENT)
@tool
def list_objectives() -> str:
    """List host-defined objectives for the current session (via ports)."""
    return get_context().ports.list_objectives()


@capability(CapabilityGroup.ENGAGEMENT)
@tool
def update_objective_status(seq: int, status: str, note: str = "") -> str:
    """Update an objective status via host ports."""
    return get_context().ports.update_objective_status(int(seq), status, note)


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
    """Record one engagement finding about a subject — not run/objective status.

    Use for discoveries about any asset class (hosts, files, malware, source,
    packages, identities, cloud, …) with evidence. kind/asset_type are free-form.
    Do NOT use for job/objective/agent progress — use update_objective_status.
    """
    return get_context().ports.record_finding(
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
    return get_context().ports.record_findings(findings_json)


@capability(CapabilityGroup.ENGAGEMENT)
@tool
def list_findings(kind: str = "") -> str:
    """List engagement findings already recorded (optional kind slug filter)."""
    return get_context().ports.list_findings(kind)


@capability(CapabilityGroup.CORE, tags={"subagent"})
@tool
def spawn_subagent(title: str, description: str, skill_names: str = "") -> str:
    """Spawn a child agent run via host ports. skill_names: comma-separated ids."""
    cfg = get_agent_config()
    ctx = get_context()
    if ctx.depth >= cfg.max_subagent_depth:
        return f"Error: subagent depth limit ({cfg.max_subagent_depth})"
    names = [n.strip() for n in (skill_names or "").split(",") if n.strip()]
    try:
        child_id = ctx.ports.spawn_child(
            title=title, description=description, skill_names=names
        )
    except Exception as exc:
        return f"Error spawning subagent: {exc}"
    _emit(
        "log",
        f"spawned subagent {child_id}: {title}",
        metadata={"event": "spawn_subagent", "child_id": child_id},
    )
    return f"spawned child job {child_id}"


@capability(CapabilityGroup.CORE, tags={"subagent"})
@tool
def wait_for_subagents(timeout_seconds: int = 600, job_ids: str = "") -> str:
    """Check whether spawned child runs finished (non-blocking; call again later)."""
    ids = [j.strip() for j in (job_ids or "").split(",") if j.strip()] or None
    try:
        return get_context().ports.wait_children(
            job_ids=ids, timeout_seconds=int(timeout_seconds)
        )
    except Exception as exc:
        return f"Error waiting for subagents: {exc}"
