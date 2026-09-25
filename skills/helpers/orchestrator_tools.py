"""Skill-side stream + optional tool RPC (Unix sockets).

Lives under ``skills/helpers/``. No peon/orchestrator imports — skills stay
runnable outside the control plane. Stream is a no-op when
ORCHESTRATOR_STREAM_SOCKET / job id are unset. MCP / skill_view RPC need
ORCHESTRATOR_RPC_SOCKET (+ ORCHESTRATOR_RPC_TOKEN) when the host bridge is up.
"""

from __future__ import annotations

import builtins
import json
import os
import socket
import threading
from typing import Any

_lock = threading.Lock()
_sock: socket.socket | None = None


def _job_id() -> str:
    return (
        os.environ.get("ORCHESTRATOR_JOB_ID", "").strip()
        or os.environ.get("ORCHESTRATOR_TASK_ID", "").strip()
    )


def stream(message_type: str, content: str, **metadata: Any) -> None:
    """NDJSON line to the orchestrator stream socket (best-effort)."""
    global _sock
    job_id = _job_id()
    path = os.environ.get("ORCHESTRATOR_STREAM_SOCKET", "").strip()
    if not job_id or not path or not content or not os.path.exists(path):
        return
    meta = dict(metadata or {})
    meta.setdefault("job_id", job_id)
    project = os.environ.get("ORCHESTRATOR_PROJECT_ID", "").strip()
    if project:
        meta.setdefault("project_id", project)
    line = (
        json.dumps(
            {
                "job_id": job_id,
                "task_id": job_id,
                "message_type": message_type,
                "content": content,
                "metadata": meta,
            }
        )
        + "\n"
    )
    try:
        with _lock:
            if _sock is None:
                _sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                _sock.settimeout(5)
                _sock.connect(path)
            _sock.sendall(line.encode())
    except OSError:
        _sock = None


def _streaming_print(*args: Any, **kwargs: Any) -> None:
    sep = kwargs.get("sep", " ")
    end = kwargs.get("end", "\n")
    text = sep.join(str(a) for a in args) + end
    builtins.print(*args, **kwargs)
    if text.strip():
        stream("stdout", text.rstrip("\n"))


# Skills that `from orchestrator_tools import print` get the streaming print.
print = _streaming_print  # noqa: A001


def _rpc_call(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    path = os.environ.get("ORCHESTRATOR_RPC_SOCKET", "").strip()
    if not path or not os.path.exists(path):
        return {
            "ok": False,
            "error": f"{tool_name} requires ORCHESTRATOR_RPC_SOCKET (host bridge)",
        }
    job_id = _job_id()
    request = json.dumps(
        {
            "tool": tool_name,
            "args": args,
            "token": os.environ.get("ORCHESTRATOR_RPC_TOKEN", ""),
            "job_id": job_id,
            "task_id": job_id,
            "project_id": os.environ.get("ORCHESTRATOR_PROJECT_ID", ""),
        }
    ) + "\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(300)
            sock.connect(path)
            sock.sendall(request.encode())
            chunks: list[bytes] = []
            while True:
                data = sock.recv(65536)
                if not data:
                    break
                chunks.append(data)
                if b"\n" in data:
                    break
        raw = b"".join(chunks).decode().strip().split("\n", 1)[0]
        return json.loads(raw) if raw else {"ok": False, "error": "empty RPC response"}
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": str(exc)}


def mcp_ensure(spec_json: str = "", name: str = "") -> dict[str, Any]:
    return _rpc_call("mcp_ensure", {"spec_json": spec_json, "name": name})


def mcp_list_tools(server: str = "") -> dict[str, Any]:
    return _rpc_call("mcp_list_tools", {"server": server})


def mcp_call(server: str, tool: str, arguments_json: str = "{}") -> dict[str, Any]:
    return _rpc_call(
        "mcp_call",
        {"server": server, "tool": tool, "arguments_json": arguments_json},
    )


def mcp_close(server: str = "") -> dict[str, Any]:
    return _rpc_call("mcp_close", {"server": server})


def skill_view(name: str, path: str = "") -> dict[str, Any]:
    return _rpc_call("skill_view", {"name": name, "path": path})
