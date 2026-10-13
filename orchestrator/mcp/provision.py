"""Start and call MCP servers inside the bound project sandbox.

The worker does not speak MCP. It materializes the catalog row and talks to
a supervisor process that lives in the same container as the server.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shlex
from typing import Any

from agent_runtime.api import Session
from orchestrator.mcp.supervisor_script import SUPERVISOR_SOURCE
from orchestrator.sandbox.materialize import materialize_tree
from orchestrator.skills.runtime.sandbox import job_role_id

logger = logging.getLogger(__name__)

_SOCK = "/tmp/peon-mcp/supervisor.sock"
_PID = "/tmp/peon-mcp/supervisor.pid"
_STAMP = "/workspace/.peon/mcp/supervisor.sha"
_SUPERVISOR = "/workspace/.peon/mcp/supervisor.py"
_CLIENT = r"""
import json, socket, sys
raw = sys.stdin.read()
sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.settimeout(float(sys.argv[2]))
sock.connect(sys.argv[1])
sock.sendall(raw.encode("utf-8") + b"\n")
buf = b""
while b"\n" not in buf:
    chunk = sock.recv(65536)
    if not chunk:
        break
    buf += chunk
    if len(buf) > 8_000_000:
        break
sys.stdout.write(buf.split(b"\n", 1)[0].decode("utf-8", "replace"))
"""


def list_mcp_tools(name: str) -> str:
    return _invoke(name, op="list_tools")


def call_mcp_tool(name: str, tool: str, arguments_json: str = "{}") -> str:
    tool_name = str(tool or "").strip()
    if not tool_name:
        return "Error: tool name is required."
    try:
        arguments = json.loads(arguments_json or "{}")
    except json.JSONDecodeError:
        return "Error: arguments_json must be a JSON object."
    if not isinstance(arguments, dict):
        return "Error: arguments_json must be a JSON object."
    return _invoke(name, op="call", tool=tool_name, arguments=arguments)


def mcp_prompt_block(role_id: str) -> str:
    """Short index of MCP servers bound to ``role_id``."""
    records = _bound(role_id)
    if not records:
        return ""
    lines = ["## MCP servers", "Call `list_mcp_tools` then `call_mcp_tool`. Servers run inside this project sandbox. Endpoint values are project configuration, not something you set."]
    for rec in records:
        required = ", ".join(name for name, req in rec.env_schema if req) or "(none)"
        lines.append(
            f"- `{rec.mcp_id}`: {rec.description or rec.command}. Required env: {required}."
        )
    return "\n".join(lines)


def _invoke(name: str, *, op: str, tool: str = "", arguments: dict | None = None) -> str:
    err = Session.require_bound()
    if err:
        return err
    session = Session.current()
    mode = (session.info.mode or "").strip().lower()
    if mode == "openshell":
        return "Error: MCP servers need the Docker sandbox."
    role_id = job_role_id(required=False)
    rec = _record_for_role(name, role_id)
    if isinstance(rec, str):
        return rec
    project_id = str(session.info.project_id or "").strip()
    env, missing = _project_env(project_id, rec)
    if missing:
        return (
            "Error: MCP server "
            f"{rec.mcp_id!r} is not configured for this project. "
            f"Missing: {', '.join(missing)}."
        )
    dest = f"/workspace/.peon/mcp/{rec.mcp_id}"
    files = dict(rec.files)
    wrote = materialize_tree(session, dest, files)
    if wrote:
        return wrote
    install, install_error = _effective_install(rec)
    if install_error:
        return install_error
    installed = _install(session, rec, dest, install)
    if installed:
        return installed
    started = _ensure_supervisor(session)
    if started:
        return started
    command, args = _runtime_command(rec, dest, install)
    cwd = dest if not rec.cwd else f"{dest}/{rec.cwd}"
    request: dict[str, Any] = {
        "op": op,
        "mcp_id": rec.mcp_id,
        "command": command,
        "args": list(args),
        "cwd": cwd,
        "env": env,
        "timeout": 60 if op == "list_tools" else 90,
    }
    if op == "call":
        request["tool"] = tool
        request["arguments"] = arguments or {}
    reply = _client(session, request)
    if not reply.get("ok"):
        return f"Error: {reply.get('error') or 'MCP call failed'}"
    if op == "list_tools":
        tools = reply.get("tools") or []
        if not tools:
            return f"{rec.mcp_id}: no tools"
        lines = [f"{rec.mcp_id} tools:"]
        for item in tools:
            if not isinstance(item, dict):
                continue
            desc = str(item.get("description") or "").strip()
            lines.append(
                f"- {item.get('name')}" + (f": {desc}" if desc else "")
            )
        return "\n".join(lines)
    return json.dumps(reply.get("result"), default=str)[:12000]


def _bound(role_id: str):
    rid = (role_id or "").strip().lower()
    if not rid:
        return []
    try:
        from peon.projects.platform_catalog import PlatformCatalog

        catalog = PlatformCatalog.shared()
        wanted = {
            b.owner_id
            for b in catalog.list_bindings(role_id=rid, owner_kind="mcp")
            if b.score >= 0.5
        }
        return [rec for rec in catalog.list_mcps() if rec.enabled and rec.mcp_id in wanted]
    except Exception:
        return []


def _record_for_role(name: str, role_id: str):
    mid = str(name or "").strip().lower()
    if not mid:
        return "Error: MCP server name is required."
    if not role_id:
        return "Error: MCP calls require a bound role."
    match = [rec for rec in _bound(role_id) if rec.mcp_id == mid]
    if not match:
        known = ", ".join(rec.mcp_id for rec in _bound(role_id)) or "(none)"
        return f"Error: MCP server {mid!r} is not bound to role {role_id}. Bound: {known}."
    try:
        from peon.projects.platform_catalog import PlatformCatalog

        full = PlatformCatalog.shared().get_mcp(mid)
    except Exception as exc:
        return f"Error: {exc}"
    if full is None or not full.enabled:
        return f"Error: MCP server {mid!r} is disabled."
    return full


def _project_env(project_id: str, rec) -> tuple[dict[str, str], list[str]]:
    stored: dict[str, str] = {}
    if project_id:
        try:
            from peon.projects.db import ensure_project_database
            from peon.projects.models import ProjectMcpConfig

            alias = ensure_project_database(project_id)
            row = (
                ProjectMcpConfig.objects.using(alias)
                .filter(project_id=project_id, mcp_id=rec.mcp_id)
                .first()
            )
            if row is not None and isinstance(row.env, dict):
                stored = {
                    str(k): str(v).strip()
                    for k, v in row.env.items()
                    if v is not None and str(v).strip()
                }
        except Exception as exc:
            logger.warning("mcp project env read failed for %s: %s", rec.mcp_id, exc)
            return {}, [f"project configuration ({exc})"]
    missing = [name for name, required in rec.env_schema if required and not stored.get(name)]
    allowed = {name for name, _req in rec.env_schema}
    env = {key: value for key, value in stored.items() if not allowed or key in allowed}
    return env, missing


def _command_name(command: str) -> str:
    return str(command or "").strip().replace("\\", "/").rsplit("/", 1)[-1]


def _effective_install(rec) -> tuple[dict | None, str]:
    """Use the catalog install, or infer one from the server's own files."""
    install = dict(rec.install) if isinstance(rec.install, dict) else {}
    kind = str(install.get("kind") or "")
    if kind in {"pip", "npm"}:
        return install, ""
    files = rec.files or {}
    has_req = "requirements.txt" in files
    has_pyproject = "pyproject.toml" in files
    has_npm = "package.json" in files
    base = _command_name(rec.command)
    if (has_req or has_pyproject) and has_npm:
        if base in {"python", "python3"}:
            has_npm = False
        elif base in {"node", "npm", "npx"}:
            has_req = has_pyproject = False
        else:
            return None, (
                "Error: this MCP server has both Python and Node manifests. "
                "Set install.kind to pip or npm."
            )
    if has_req or has_pyproject:
        manifest = "requirements.txt" if has_req else "pyproject.toml"
        return {"kind": "pip", "manifest": manifest}, ""
    if has_npm:
        return {"kind": "npm", "manifest": "package.json"}, ""
    return None, ""


def _runtime_command(rec, dest: str, install: dict | None) -> tuple[str, tuple[str, ...]]:
    install = install or {}
    kind = str(install.get("kind") or "")
    base = _command_name(rec.command)
    if kind == "pip" and base in {"python", "python3"}:
        return f"/workspace/.peon/mcp-venvs/{rec.mcp_id}/bin/python", rec.args
    # node, npm, and npx stay on PATH. A bare package binary is local.
    if (
        kind == "npm"
        and base
        and base not in {"node", "npm", "npx"}
        and "/" not in str(rec.command)
        and "." not in base
    ):
        return f"{dest}/node_modules/.bin/{base}", rec.args
    return rec.command, rec.args


def _install_digest(rec, install: dict | None) -> str:
    payload = json.dumps(
        {"install": install, "files": rec.files},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _marker_current(session, marker: str, digest: str) -> bool:
    probe = session.exec(
        ["sh", "-c", f"cat {shlex.quote(marker)} 2>/dev/null"],
        timeout=15,
    )
    return bool(probe.ok and digest == (probe.stdout or "").strip())


def _write_marker(session, marker: str, digest: str) -> None:
    session.exec(
        ["sh", "-c", f"printf %s {shlex.quote(digest)} > {shlex.quote(marker)}"],
        timeout=15,
    )


def _install(session, rec, dest: str, install: dict | None) -> str:
    install = install or {}
    kind = str(install.get("kind") or "")
    if kind not in {"pip", "npm"}:
        return ""
    manifest = str(install.get("manifest") or "")
    if manifest and manifest not in (rec.files or {}):
        return f"Error: MCP install manifest {manifest!r} is not in the server files."
    digest = _install_digest(rec, install)
    if kind == "pip":
        venv = f"/workspace/.peon/mcp-venvs/{rec.mcp_id}"
        python = f"{venv}/bin/python"
        marker = f"{venv}/.peon-ready"
        if session.exec(["test", "-x", python], timeout=15).ok and _marker_current(
            session, marker, digest
        ):
            return ""
        made = session.exec(["python3", "-m", "venv", venv], timeout=180)
        if not made.ok:
            return "Error: could not create the MCP virtualenv in the sandbox."
        req = f"{dest}/{manifest}" if manifest else ""
        if manifest.endswith("pyproject.toml") or not manifest:
            cmd = [python, "-m", "pip", "install", "--disable-pip-version-check", "-e", dest]
        else:
            cmd = [python, "-m", "pip", "install", "--disable-pip-version-check", "-r", req]
        installed = session.exec(cmd, cwd=dest, timeout=1200)
        if not installed.ok:
            detail = (installed.stderr or installed.stdout or "pip failed").strip()
            return f"Error: MCP dependency install failed: {detail[:800]}"
        _write_marker(session, marker, digest)
        return ""
    if not session.which("npm"):
        return "Error: npm is not available in the sandbox image."
    marker = f"{dest}/.peon-npm-ready"
    if _marker_current(session, marker, digest):
        return ""
    installed = session.exec(
        ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"],
        cwd=dest,
        timeout=1200,
    )
    if not installed.ok:
        detail = (installed.stderr or installed.stdout or "npm failed").strip()
        return f"Error: MCP dependency install failed: {detail[:800]}"
    _write_marker(session, marker, digest)
    return ""


def _supervisor_stamp() -> str:
    return hashlib.sha256(SUPERVISOR_SOURCE.encode("utf-8")).hexdigest()


def _supervisor_alive(session, stamp: str) -> bool:
    script = (
        f"pid=$(cat {shlex.quote(_PID)} 2>/dev/null || true); "
        f"have=$(cat {shlex.quote(_STAMP)} 2>/dev/null || true); "
        f"if [ -n \"$pid\" ] && [ \"$have\" = {shlex.quote(stamp)} ] "
        f"&& [ -S {shlex.quote(_SOCK)} ] && kill -0 \"$pid\" 2>/dev/null; then "
        "echo up; else echo down; fi"
    )
    probe = session.exec(["sh", "-c", script], timeout=15)
    return (probe.stdout or "").strip() == "up"


def _ensure_supervisor(session) -> str:
    stamp = _supervisor_stamp()
    if _supervisor_alive(session, stamp):
        return ""
    wrote = materialize_tree(
        session,
        "/workspace/.peon/mcp",
        {"supervisor.py": SUPERVISOR_SOURCE},
    )
    if wrote:
        return wrote
    launch = "\n".join(
        [
            "mkdir -p /tmp/peon-mcp /workspace/.peon/mcp",
            "exec 9>/tmp/peon-mcp/supervisor.lock",
            "flock -w 20 9 || exit 1",
            f"pid=$(cat {shlex.quote(_PID)} 2>/dev/null || true)",
            f"have=$(cat {shlex.quote(_STAMP)} 2>/dev/null || true)",
            f"if [ -n \"$pid\" ] && [ \"$have\" = {shlex.quote(stamp)} ] "
            f"&& [ -S {shlex.quote(_SOCK)} ] && kill -0 \"$pid\" 2>/dev/null; then",
            "  exit 0",
            "fi",
            "if [ -n \"$pid\" ]; then kill \"$pid\" 2>/dev/null || true; sleep 0.2; fi",
            f"rm -f {shlex.quote(_SOCK)} {shlex.quote(_PID)}",
            f"printf %s {shlex.quote(stamp)} > {shlex.quote(_STAMP)}",
            "setsid nohup python3 /workspace/.peon/mcp/supervisor.py "
            ">/tmp/peon-mcp/supervisor.log 2>&1 < /dev/null &",
        ]
    )
    launched = session.exec(["sh", "-c", launch], timeout=30)
    if not launched.ok:
        detail = (launched.stderr or launched.stdout or "supervisor launch failed").strip()
        return f"Error: could not start the MCP supervisor in the sandbox: {detail[:400]}"
    for _ in range(25):
        if _supervisor_alive(session, stamp):
            return ""
        session.exec(["sleep", "0.2"], timeout=5)
    log = session.exec(["sh", "-c", "tail -n 40 /tmp/peon-mcp/supervisor.log"], timeout=15)
    detail = (log.stdout or log.stderr or "supervisor socket did not appear").strip()
    return f"Error: MCP supervisor did not start: {detail[:500]}"


def _client(session, request: dict) -> dict:
    try:
        limit = float(request.get("timeout") or 90)
    except (TypeError, ValueError):
        limit = 90
    result = session.exec(
        ["python3", "-c", _CLIENT, _SOCK, str(int(limit) + 5)],
        stdin=json.dumps(request),
        timeout=limit + 20,
    )
    raw = (result.stdout or "").strip()
    if not raw:
        detail = (result.stderr or "empty supervisor response").strip()
        return {"ok": False, "error": detail[:500]}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "error": raw[:500]}
    if not isinstance(data, dict):
        return {"ok": False, "error": "supervisor returned a non-object"}
    return data
