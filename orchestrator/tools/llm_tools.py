"""LangChain capability tools for the Job agent (sandbox-only)."""

from __future__ import annotations

from langchain_core.tools import tool

from orchestrator.agent.context import get_config, get_context
from orchestrator.capabilities.registry import CapabilityGroup, capability
from orchestrator.runtime.shell import ShellRunner
from orchestrator.sandbox import SandboxSession
from orchestrator.skills.execute import SkillExecutionDispatcher, SkillRunRequest
from orchestrator.skills.misc.catalog import filter_skills
from orchestrator.skills.misc.registry import SkillRegistry

_DOCKER = frozenset({"docker", "shared"})


def require_sandbox() -> str | None:
    """Return an error string if no Docker/shared sandbox is bound."""
    info = SandboxSession.current().info
    mode = (info.mode or "").strip().lower()
    if mode not in _DOCKER:
        return (
            "Error: no Docker sandbox bound (host execution disabled). "
            f"mode={mode!r}"
        )
    return None


def sandbox_summary() -> str:
    info = SandboxSession.current().info
    return f"name={info.name} mode={info.mode} project_id={info.project_id or '-'}"


_registered = False


def ensure_tools_registered() -> None:
    """Idempotent import side-effect registration."""
    global _registered
    if _registered:
        return
    _registered = True
    # Touch tool callables so @capability decorators run.
    _ = (
        sandbox_setup,
        sandbox_status,
        provision_cli,
        run_cli,
        run_skill_script,
        skills_list,
        skill_view,
        list_objectives,
        update_objective_status,
        record_finding,
        record_findings,
        list_findings,
        spawn_subagent,
        wait_for_subagents,
    )


@capability(CapabilityGroup.SANDBOX)
@tool
def sandbox_setup() -> str:
    """Confirm the project Docker sandbox is bound (peon provisions before the agent)."""
    err = require_sandbox()
    if err:
        return err
    return "Sandbox ready — " + sandbox_summary()


@capability(CapabilityGroup.SANDBOX)
@tool
def sandbox_status() -> str:
    """Show bound sandbox mode and name."""
    err = require_sandbox()
    if err:
        return err
    info = SandboxSession.current().info
    lines = [sandbox_summary()]
    try:
        which = SandboxSession.current().which
        for binary in ("nmap", "httpx", "python3"):
            lines.append(f"{binary}={'yes' if which(binary) else 'no'}")
    except Exception:
        pass
    return "\n".join(lines)


@capability(CapabilityGroup.SANDBOX)
@tool
def provision_cli(binary: str, package: str = "", skill_name: str = "") -> str:
    """Install/verify a CLI in the sandbox (catalog → skill docs → LLM recipe)."""
    err = require_sandbox()
    if err:
        return err
    name = (binary or "").strip()
    if not name:
        return "Error: provide a binary name."
    from orchestrator.runtime.resolve import InstallResolver

    ok, msg = InstallResolver.shared().resolve(
        name, package=package, skill_name=skill_name
    )
    if ok:
        get_context().ports.emit("log", msg or f"provisioned {name}")
        return msg or f"provisioned {name}"
    return f"Error: {msg}"


@capability(CapabilityGroup.SANDBOX)
@tool
def run_cli(command: str) -> str:
    """Run an ad-hoc shell command in the bound Docker sandbox (RoE applies)."""
    err = require_sandbox()
    if err:
        return err
    cmd = (command or "").strip()
    if not cmd:
        return "Error: empty command."
    get_context().ports.emit("tool", f"run_cli: {cmd[:200]}")
    code = ShellRunner.shared().run_shell(cmd)
    return f"exit={code}"


@capability(CapabilityGroup.SKILLS)
@tool
def run_skill_script(
    skill_name: str, script: str = "scripts/run.py", command: str = ""
) -> str:
    """Run a Peon skill script (skills/<name>/scripts/…) inside the sandbox."""
    err = require_sandbox()
    if err:
        return err
    skill = (skill_name or "").strip()
    path = (script or "scripts/run.py").strip() or "scripts/run.py"
    if not skill:
        return "Error: skill_name required."
    get_context().ports.emit(
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
    """List jobable skills from the Peon filesystem catalog."""
    skills = filter_skills(
        SkillRegistry.shared().get_registry().values(), jobable_only=True
    )
    if not skills:
        return "No jobable skills."
    lines = []
    for s in sorted(skills, key=lambda x: x.name):
        cat = s.category or "-"
        lines.append(f"{s.name} [{cat}] — {(s.description or '')[:120]}")
    return "\n".join(lines)


@capability(CapabilityGroup.SKILLS)
@tool
def skill_view(name: str, path: str = "") -> str:
    """Show a skill's instructions (or a reference file under the skill dir)."""
    del path  # reserved for future reference path reads
    skill = SkillRegistry.shared().load_skill((name or "").strip())
    if skill is None:
        return f"Skill not found: {name!r}"
    body = (skill.instructions or "").strip() or skill.description or ""
    tools = " ".join(skill.tools or [])
    return (
        f"name: {skill.name}\n"
        f"category: {skill.category or '-'}\n"
        f"allowed-tools: {tools or '-'}\n"
        f"requires_clis: {', '.join(skill.toolkit or []) or '-'}\n\n"
        f"{body[:6000]}"
    )


@capability(CapabilityGroup.ENGAGEMENT)
@tool
def list_objectives() -> str:
    """List objectives for the current project."""
    return get_context().ports.list_objectives()


@capability(CapabilityGroup.ENGAGEMENT)
@tool
def update_objective_status(seq: int, status: str, note: str = "") -> str:
    """Update an objective status (pending|in_progress|completed|blocked|cancelled)."""
    return get_context().ports.update_objective_status(int(seq), status, note)


@capability(CapabilityGroup.ENGAGEMENT)
@tool
def record_finding(
    title: str,
    severity: str = "info",
    kind: str = "observation",
    evidence: str = "",
    host: str = "",
) -> str:
    """Record one finding for the project."""
    return get_context().ports.record_finding(
        title=title,
        severity=severity,
        kind=kind,
        evidence=evidence,
        host=host,
    )


@capability(CapabilityGroup.ENGAGEMENT)
@tool
def record_findings(findings_json: str) -> str:
    """Record multiple findings from a JSON list."""
    return get_context().ports.record_findings(findings_json)


@capability(CapabilityGroup.ENGAGEMENT)
@tool
def list_findings(kind: str = "") -> str:
    """List findings for the current project."""
    return get_context().ports.list_findings(kind)


@capability(CapabilityGroup.CORE, tags={"subagent"})
@tool
def spawn_subagent(title: str, description: str, skill_names: str = "") -> str:
    """Spawn a child Job agent. skill_names: comma-separated catalog skill ids."""
    cfg = get_config()
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
    ctx.ports.emit(
        "log",
        f"spawned subagent {child_id}: {title}",
        metadata={"event": "spawn_subagent", "child_id": child_id},
    )
    return f"spawned child job {child_id}"


@capability(CapabilityGroup.CORE, tags={"subagent"})
@tool
def wait_for_subagents(timeout_seconds: int = 600, job_ids: str = "") -> str:
    """Check whether spawned child Jobs finished (non-blocking; call again later)."""
    ids = [j.strip() for j in (job_ids or "").split(",") if j.strip()] or None
    try:
        return get_context().ports.wait_children(
            job_ids=ids, timeout_seconds=int(timeout_seconds)
        )
    except Exception as exc:
        return f"Error waiting for subagents: {exc}"
