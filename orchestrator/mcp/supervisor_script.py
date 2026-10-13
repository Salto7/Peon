"""Self-contained MCP supervisor. Runs inside the project sandbox.

Speaks newline-delimited JSON on a Unix socket. Speaks MCP stdio
(Content-Length framing) to each child. No Peon imports.
"""

SUPERVISOR_SOURCE = r'''#!/usr/bin/env python3
"""In-sandbox MCP supervisor. One process per project container."""
from __future__ import annotations

import json
import os
import select
import socket
import subprocess
from pathlib import Path

SOCK = os.environ.get("PEON_MCP_SOCK", "/tmp/peon-mcp/supervisor.sock")
PID = os.environ.get("PEON_MCP_PID", "/tmp/peon-mcp/supervisor.pid")
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
# Project config must not replace the sandbox runtime environment.
_RESERVED_ENV = {
    "PATH",
    "HOME",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "PYTHONPATH",
    "NODE_OPTIONS",
    "PYTHONHOME",
}


class Child:
    def __init__(self, proc, fp, err=None, nxt=1):
        self.proc = proc
        self.fp = fp
        self.err = err
        self.nxt = nxt


def _fingerprint(spec: dict) -> str:
    return json.dumps(
        {
            "command": spec.get("command") or "",
            "args": spec.get("args") or [],
            "cwd": spec.get("cwd") or "",
            "env": spec.get("env") or {},
        },
        sort_keys=True,
        default=str,
    )


def _stop(child: Child) -> None:
    if child.proc.poll() is None:
        try:
            child.proc.kill()
        except OSError:
            pass
        try:
            child.proc.wait(timeout=2)
        except Exception:
            pass
    if child.err is not None:
        try:
            child.err.close()
        except OSError:
            pass


CHILDREN = {}


def _frame(payload: dict) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


def _read_frame(proc, timeout):
    buf = b""
    while b"\r\n\r\n" not in buf:
        ready, _, _ = select.select([proc.stdout], [], [], timeout)
        if not ready:
            raise TimeoutError("MCP server header timeout")
        chunk = proc.stdout.read(1)
        if not chunk:
            raise EOFError("MCP server closed stdout")
        buf += chunk
        if len(buf) > 65536:
            raise ValueError("MCP header too large")
    header, rest = buf.split(b"\r\n\r\n", 1)
    length = 0
    for line in header.decode("ascii", "replace").split("\r\n"):
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1].strip())
    if length <= 0 or length > 8_000_000:
        raise ValueError("bad Content-Length")
    while len(rest) < length:
        ready, _, _ = select.select([proc.stdout], [], [], timeout)
        if not ready:
            raise TimeoutError("MCP server body timeout")
        chunk = proc.stdout.read(length - len(rest))
        if not chunk:
            raise EOFError("MCP server closed during body")
        rest += chunk
    return json.loads(rest[:length].decode("utf-8"))


def _send(child: Child, payload: dict) -> None:
    child.proc.stdin.write(_frame(payload))
    child.proc.stdin.flush()


def _answer_server(child: Child, msg: dict) -> None:
    """Reply to a server-initiated request so the session cannot deadlock."""
    method = str(msg.get("method") or "")
    mid = msg.get("id")
    if mid is None:
        return
    if method == "ping":
        result = {}
    elif method == "roots/list":
        result = {"roots": []}
    else:
        _send(
            child,
            {
                "jsonrpc": "2.0",
                "id": mid,
                "error": {"code": -32601, "message": f"unsupported: {method}"},
            },
        )
        return
    _send(child, {"jsonrpc": "2.0", "id": mid, "result": result})


def _rpc(child: Child, method: str, params: dict | None, *, note=False, timeout=60):
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if not note:
        msg["id"] = f"c{child.nxt}"
        child.nxt += 1
    _send(child, msg)
    if note:
        return None
    while True:
        if child.proc.poll() is not None:
            raise EOFError("MCP server exited")
        reply = _read_frame(child.proc, timeout)
        if not isinstance(reply, dict):
            raise ValueError("MCP server sent a non-object")
        if reply.get("method"):
            _answer_server(child, reply)
            continue
        if reply.get("id") != msg["id"]:
            continue
        if "error" in reply:
            raise RuntimeError(str(reply["error"]))
        return reply.get("result")


def _timeout(spec: dict, default: float) -> float:
    try:
        value = float(spec.get("timeout") or default)
    except (TypeError, ValueError):
        value = default
    return max(5.0, min(value, 600.0))


def _start_process(spec: dict) -> Child:
    command = str(spec.get("command") or "").strip()
    args = [str(a) for a in (spec.get("args") or [])]
    if not command:
        raise ValueError("command is required")
    cwd = str(spec.get("cwd") or "/workspace")
    if not cwd.startswith("/workspace"):
        raise ValueError("cwd must stay inside the sandbox workspace")
    env = os.environ.copy()
    extra = spec.get("env") or {}
    if isinstance(extra, dict):
        for key, value in extra.items():
            name = str(key or "").strip()
            if not name or name in _RESERVED_ENV:
                continue
            env[name] = str(value)
    Path(cwd).mkdir(parents=True, exist_ok=True)
    log_dir = Path("/tmp/peon-mcp")
    log_dir.mkdir(parents=True, exist_ok=True)
    raw_id = str(spec.get("mcp_id") or "server")
    safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in raw_id) or "server"
    err = open(log_dir / f"{safe_id}.log", "ab")
    proc = subprocess.Popen(
        [command, *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=err,
        cwd=cwd,
        env=env,
        bufsize=0,
    )
    return Child(proc, _fingerprint(spec), err)


def _spawn(spec: dict) -> Child:
    timeout = _timeout(spec, 30)
    last = "initialize failed"
    for version in PROTOCOLS:
        child = _start_process(spec)
        try:
            _rpc(
                child,
                "initialize",
                {
                    "protocolVersion": version,
                    "capabilities": {},
                    "clientInfo": {"name": "peon", "version": "1"},
                },
                timeout=timeout,
            )
            _rpc(child, "notifications/initialized", {}, note=True, timeout=timeout)
            return child
        except Exception as exc:
            last = str(exc)
            _stop(child)
    raise RuntimeError(last)


def _child(spec: dict) -> Child:
    mcp_id = str(spec.get("mcp_id") or "").strip()
    if not mcp_id:
        raise ValueError("mcp_id is required")
    current = CHILDREN.get(mcp_id)
    fp = _fingerprint(spec)
    if current is not None and current.proc.poll() is None and current.fp == fp:
        return current
    if current is not None:
        _stop(current)
    child = _spawn(spec)
    CHILDREN[mcp_id] = child
    return child


def _list_tools(child: Child, timeout: float) -> list:
    tools = []
    cursor = ""
    while True:
        params = {"cursor": cursor} if cursor else {}
        result = _rpc(child, "tools/list", params, timeout=timeout) or {}
        batch = result.get("tools") or []
        if isinstance(batch, list):
            tools.extend(item for item in batch if isinstance(item, dict))
        cursor = str(result.get("nextCursor") or "")
        if not cursor or len(tools) >= 500:
            return tools


def _handle(req: dict) -> dict:
    op = str(req.get("op") or "")
    if op == "list_tools":
        child = _child(req)
        tools = _list_tools(child, _timeout(req, 60))
        return {"ok": True, "tools": tools}
    if op == "call":
        child = _child(req)
        tool = str(req.get("tool") or "").strip()
        if not tool:
            raise ValueError("tool is required")
        arguments = req.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        result = _rpc(
            child,
            "tools/call",
            {"name": tool, "arguments": arguments},
            timeout=_timeout(req, 90),
        )
        return {"ok": True, "result": result}
    if op == "close":
        mcp_id = str(req.get("mcp_id") or "")
        current = CHILDREN.pop(mcp_id, None)
        if current is not None:
            _stop(current)
        return {"ok": True}
    raise ValueError(f"unknown op {op!r}")


def serve():
    path = Path(SOCK)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    Path(PID).write_text(str(os.getpid()), encoding="utf-8")
    server.listen(16)
    while True:
        conn, _ = server.accept()
        try:
            buf = b""
            while b"\n" not in buf:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > 4_000_000:
                    break
            line = buf.split(b"\n", 1)[0]
            try:
                req = json.loads(line.decode("utf-8")) if line else {}
                if not isinstance(req, dict):
                    raise ValueError("request must be an object")
                reply = _handle(req)
            except Exception as exc:
                reply = {"ok": False, "error": str(exc)}
            conn.sendall((json.dumps(reply, default=str) + "\n").encode("utf-8"))
        finally:
            try:
                conn.close()
            except OSError:
                pass


if __name__ == "__main__":
    serve()
'''
