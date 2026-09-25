"""Unix-socket RPC server so sandboxed skills can call host tools.

Protocol matches ``skills/helpers/orchestrator_tools.py``:

* Client connects per call (or keeps a persistent socket) and sends one JSON
  line: ``{"tool", "args", "token", "job_id", "task_id", "project_id"}``.
* Server replies with one JSON line.

Adapted from the pentest_AI / Peon host-bridge contract (Django-free). Peon
itself only shipped the skill-side client; this module is the matching server.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

MAX_TOOL_CALLS = 50
_RECV_SIZE = 65_536
_ACCEPT_TIMEOUT = 0.5
_CLIENT_TIMEOUT = 300.0
_LISTEN_BACKLOG = 32
_HANDLER_WORKERS = 8
_MAX_BUFFER = 4 * 1024 * 1024

RpcHandler = Callable[..., Any]

DEFAULT_RPC_TOOLS = frozenset(
    {
        "skill_view",
        "mcp_ensure",
        "mcp_list_tools",
        "mcp_call",
        "mcp_close",
        "sandbox_run",
    }
)


def normalize_run_command_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "exit_code": int(result.get("exit_code", -1)),
        "stdout": result.get("stdout") or "",
        "stderr": result.get("stderr") or result.get("error") or "",
        **({"error": result["error"]} if result.get("error") else {}),
    }


def default_rpc_handlers() -> dict[str, RpcHandler]:
    """Built-in handlers the library can serve without a host control plane."""

    def skill_view(name: str = "", path: str = "") -> str:
        from orchestrator.skills.misc.registry import SkillRegistry

        skill = SkillRegistry.shared().load_skill((name or "").strip())
        if skill is None:
            return f"Skill not found: {name!r}"
        return skill.format_view(path=path)

    def _mcp_missing(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {
            "ok": False,
            "error": "MCP RPC handlers not registered — pass handlers= to RpcServer.start",
        }

    return {
        "skill_view": skill_view,
        "mcp_ensure": _mcp_missing,
        "mcp_list_tools": _mcp_missing,
        "mcp_call": _mcp_missing,
        "mcp_close": _mcp_missing,
    }


@dataclass
class RpcServer:
    """Accept AF_UNIX RPC clients and dispatch tool calls."""

    socket_path: str
    job_id: str = ""
    handlers: dict[str, RpcHandler] = field(default_factory=dict)
    allowed_tools: frozenset[str] = field(default_factory=lambda: DEFAULT_RPC_TOOLS)
    max_tool_calls: int = MAX_TOOL_CALLS
    sock_mode: int = 0o666
    token: str = ""
    on_call: Callable[[str, dict[str, Any], dict[str, Any]], None] | None = None

    _server: socket.socket | None = field(default=None, init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _pool: ThreadPoolExecutor | None = field(default=None, init=False, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _counter: list[int] = field(default_factory=lambda: [0], init=False, repr=False)
    _running: bool = field(default=False, init=False, repr=False)

    @classmethod
    def start(
        cls,
        socket_path: str,
        *,
        job_id: str = "",
        handlers: dict[str, RpcHandler] | None = None,
        allowed_tools: frozenset[str] | None = None,
        max_tool_calls: int = MAX_TOOL_CALLS,
        sock_mode: int = 0o666,
        token: str | None = None,
        on_call: Callable[[str, dict[str, Any], dict[str, Any]], None] | None = None,
    ) -> RpcServer:
        """Bind socket, start accept loop, return running server (with ``.token``)."""
        merged = dict(default_rpc_handlers())
        if handlers:
            merged.update(handlers)
        tools = allowed_tools if allowed_tools is not None else frozenset(merged) | DEFAULT_RPC_TOOLS
        server = cls(
            socket_path=socket_path,
            job_id=job_id,
            handlers=merged,
            allowed_tools=tools,
            max_tool_calls=max_tool_calls,
            sock_mode=sock_mode,
            token=token if token is not None else secrets.token_urlsafe(32),
            on_call=on_call,
        )
        server._start()
        return server

    def _start(self) -> None:
        if self._running:
            return
        parent = os.path.dirname(self.socket_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

        self._pool = ThreadPoolExecutor(
            max_workers=_HANDLER_WORKERS, thread_name_prefix="rpc-sock"
        )
        self._stop.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._serve, name="rpc-sock-accept", daemon=True
        )
        self._thread.start()
        logger.info("RPC Unix socket listening on %s", self.socket_path)

    def stop(self) -> None:
        self._running = False
        self._stop.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass

    @property
    def tool_calls(self) -> int:
        return int(self._counter[0])

    def _serve(self) -> None:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server = server
        try:
            server.bind(self.socket_path)
            os.chmod(self.socket_path, self.sock_mode)
            server.listen(_LISTEN_BACKLOG)
            server.settimeout(_ACCEPT_TIMEOUT)
            while self._running and not self._stop.is_set():
                try:
                    conn, _ = server.accept()
                except TimeoutError:
                    continue
                except OSError:
                    if self._running:
                        logger.exception("RPC socket accept error")
                    break
                pool = self._pool
                if pool is None:
                    conn.close()
                    continue
                pool.submit(self._handle_client, conn)
        finally:
            try:
                server.close()
            except OSError:
                pass

    def _handle_client(self, conn: socket.socket) -> None:
        buf = b""
        try:
            conn.settimeout(_CLIENT_TIMEOUT)
            while self._running and not self._stop.is_set():
                try:
                    chunk = conn.recv(_RECV_SIZE)
                except TimeoutError:
                    break
                if not chunk:
                    break
                buf += chunk
                if len(buf) > _MAX_BUFFER:
                    logger.warning("RPC client exceeded buffer; closing")
                    break
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    line = raw.strip()
                    if not line:
                        continue
                    reply = self._dispatch_line(line)
                    try:
                        conn.sendall((json.dumps(reply) + "\n").encode())
                    except OSError:
                        return
        except (ConnectionResetError, OSError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _dispatch_line(self, line: bytes) -> dict[str, Any]:
        try:
            request = json.loads(line.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return {"ok": False, "error": str(exc)}

        if not isinstance(request, dict):
            return {"ok": False, "error": "RPC request must be a JSON object"}

        if not secrets.compare_digest(str(request.get("token") or ""), self.token):
            return {"ok": False, "error": "Unauthorized"}

        tool_name = str(request.get("tool") or "").strip()
        tool_args = request.get("args") or {}
        if not isinstance(tool_args, dict):
            tool_args = {}

        meta = {
            "job_id": str(
                request.get("job_id") or request.get("task_id") or self.job_id or ""
            ).strip(),
            "project_id": str(request.get("project_id") or "").strip(),
        }

        if tool_name == "run_command":
            tool_name = "sandbox_run"
            tool_args = {"command": tool_args.get("command", "")}

        if tool_name not in self.allowed_tools:
            return {
                "ok": False,
                "error": (
                    f"Tool '{tool_name}' not available over RPC. "
                    f"Allowed: {', '.join(sorted(self.allowed_tools))}"
                ),
            }

        if self._counter[0] >= self.max_tool_calls:
            return {
                "ok": False,
                "error": f"Tool call limit ({self.max_tool_calls}) reached",
            }

        self._counter[0] += 1
        if self.on_call:
            try:
                self.on_call(tool_name, tool_args, meta)
            except Exception:
                logger.exception("RPC on_call failed for %s", tool_name)

        handler = self.handlers.get(tool_name)
        if handler is None:
            return {"ok": False, "error": f"Unknown tool: {tool_name}"}

        try:
            raw = handler(**tool_args)
        except TypeError:
            # Some hosts register (args: dict) -> Any callables.
            try:
                raw = handler(tool_args)  # type: ignore[misc]
            except Exception as exc:
                return normalize_run_command_result(
                    {
                        "exit_code": -1,
                        "stdout": "",
                        "stderr": str(exc),
                        "error": str(exc),
                    }
                )
        except Exception as exc:
            return normalize_run_command_result(
                {
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": str(exc),
                    "error": str(exc),
                }
            )

        if tool_name in ("run_command", "sandbox_run", "sandbox_install"):
            if isinstance(raw, dict):
                return normalize_run_command_result(raw)
            return normalize_run_command_result(
                {"exit_code": -1, "stdout": "", "stderr": str(raw), "error": str(raw)}
            )
        if tool_name == "skill_view":
            return {"content": raw if isinstance(raw, str) else str(raw)}
        if isinstance(raw, dict):
            return raw
        return {"result": raw}
