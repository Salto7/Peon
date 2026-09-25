"""Shell runtime: stream emit, CLI provision, run / periodic."""

from __future__ import annotations

import json
import os
import re
import shlex
import socket
import sys
import time

from orchestrator.utils.job_env import JobEnv
from orchestrator.prompts import INSTALL_MISSING_HINT
from orchestrator.sandbox import SandboxSession
from orchestrator.utils.service import SharedService

# Compound / redirected shell is executed as-is — never treated as an apt binary.
_COMPOUND_SHELL = re.compile(r"(?:&&|\|\||[;|`\n<>]|\$\(|\$\{)")
_BIN_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9._+-]*$")
# POSIX / common shell builtins — skip install resolver (generic, not skill-specific).
#ugly, remove when we have a better way to handle builtins
_SHELL_BUILTINS = frozenset(
    {
        ".",
        ":",
        "[",
        "alias",
        "bg",
        "break",
        "builtin",
        "cd",
        "command",
        "continue",
        "declare",
        "echo",
        "eval",
        "exec",
        "exit",
        "export",
        "false",
        "fg",
        "getopts",
        "hash",
        "jobs",
        "kill",
        "local",
        "printf",
        "pwd",
        "read",
        "readonly",
        "return",
        "set",
        "shift",
        "source",
        "test",
        "times",
        "trap",
        "true",
        "type",
        "ulimit",
        "umask",
        "unalias",
        "unset",
        "wait",
    }
)


class StreamEmitter(SharedService):
    """Push job messages to ORCHESTRATOR_STREAM_SOCKET when present."""

    def stream(self, message_type: str, content: str, **metadata) -> None:
        job_id = JobEnv.get("ORCHESTRATOR_JOB_ID")
        path = JobEnv.get("ORCHESTRATOR_STREAM_SOCKET")
        if not job_id or not path or not content or not os.path.exists(path):
            return
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(5)
                sock.connect(path)
                sock.sendall(
                    (
                        json.dumps(
                            {
                                "job_id": job_id,
                                "message_type": message_type,
                                "content": content,
                                "metadata": metadata or {},
                            }
                        )
                        + "\n"
                    ).encode()
                )
        except OSError:
            pass

    def emit_stdout(self, text: str) -> None:
        self._emit("stdout", text)

    def emit_stderr(self, text: str) -> None:
        self._emit("stderr", text, file=sys.stderr)

    def emit_log(self, text: str) -> None:
        if not text:
            return
        self.stream("log", text)
        print(text, flush=True)

    def _emit(self, kind: str, text: str, *, file=None) -> None:
        if not text:
            return
        self.stream(kind, text.rstrip("\n"))
        print(
            text,
            end="" if text.endswith("\n") else "\n",
            file=file or sys.stdout,
            flush=True,
        )


class ProvisionService(SharedService):
    """Resolve and install binaries for skill/runtime shell runs."""

    @staticmethod
    def command_binary(command: str) -> str:
        """Return a provisionable binary name, or ``\"\"`` to skip install.

        Only simple ``binary [args…]`` forms qualify. Compound shell, redirects,
        substitutions, and assignments are executed without a provision pass.
        """
        cmd = (command or "").strip()
        if not cmd or _COMPOUND_SHELL.search(cmd):
            return ""
        if re.match(r"^\w+=", cmd):
            return ""
        try:
            tokens = shlex.split(cmd, posix=True)
        except ValueError:
            return ""
        if not tokens:
            return ""
        token = tokens[0]
        if token.startswith("-") or "/" in token or "\\" in token:
            return ""
        if not _BIN_NAME.fullmatch(token):
            return ""
        return token

    def provision_command(self, command: str, *, package: str = "") -> tuple[bool, str]:
        binary = self.command_binary(command)
        if not binary:
            return True, ""
        if binary in _SHELL_BUILTINS:
            return True, ""
        if SandboxSession.current().which(binary):
            return True, f"{binary} already installed"
        if binary in SandboxSession.current().base_commands() and not package:
            return False, f"image base binary {binary!r} missing from PATH"
        from orchestrator.runtime.resolve import InstallResolver

        skill = (os.environ.get("ORCHESTRATOR_SKILL_NAME") or "").strip()
        ok, msg = InstallResolver.shared().resolve(
            binary, package=package, skill_name=skill
        )
        if not ok and INSTALL_MISSING_HINT not in msg:
            msg = f"{msg}. {INSTALL_MISSING_HINT}"
        return ok, msg


class ShellRunner(SharedService):
    """Run provisioned shell commands through the bound sandbox."""

    def __init__(
        self,
        *,
        provision: ProvisionService | None = None,
        stream: StreamEmitter | None = None,
    ) -> None:
        self._provision = provision or ProvisionService.shared()
        self._stream = stream or StreamEmitter.shared()

    def _announce(self, ok: bool, msg: str) -> int | None:
        if msg and "already" not in msg:
            self._stream.emit_log(msg)
        if not ok:
            if msg:
                self._stream.emit_stderr(msg)
            return 1
        return None

    def _run_shell(self, cmd: str, *, timeout: float | None = None) -> int:
        res = SandboxSession.current().exec(cmd, timeout=timeout, shell=True)
        if res.stdout:
            self._stream.emit_stdout(res.stdout)
        if res.stderr:
            self._stream.emit_stderr(res.stderr)
        return res.code if res.code is not None else 1

    def run_shell(self, command: str, *, package: str = "") -> int:
        cmd = (command or "").strip()
        if not cmd:
            self._stream.emit_stderr("usage: '<shell command>' [apt_package]")
            return 2
        err = self._announce(*self._provision.provision_command(cmd, package=package))
        return err if err is not None else self._run_shell(cmd)

    def run_periodic(
        self,
        command: str,
        *,
        interval_seconds: int,
        duration_seconds: int,
        package: str = "",
    ) -> int:
        cmd = (command or "").strip()
        if not cmd:
            self._stream.emit_stderr(
                "usage: command + interval_seconds + duration_seconds"
            )
            return 2
        interval = max(1, int(interval_seconds))
        total = max(interval, int(duration_seconds))
        ok, msg = self._provision.provision_command(cmd, package=package)
        if msg and "already" not in msg:
            self._stream.emit_log(msg)
        if not ok:
            if msg:
                self._stream.emit_stderr(msg)
            return 1
        deadline = time.time() + total
        tick = min(max(interval * 2, 60), 600)
        iteration = last = 0
        self._stream.emit_log(f"Periodic: {cmd!r} every {interval}s for {total}s")
        while time.time() < deadline:
            iteration += 1
            self._stream.emit_log(f"--- run {iteration}: {cmd} ---")
            last = self._run_shell(cmd, timeout=tick)
            self._stream.stream(
                "log", f"exit_code={last}", iteration=iteration, timed_out=last == 124
            )
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            time.sleep(min(interval, remaining))
        self._stream.emit_log(f"--- finished {iteration} run(s) in {total}s ---")
        return last
