"""BinaryRunner — run a native CLI once per resolved command."""

from __future__ import annotations

import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from binary import NativeBinaryResolver
from context import PackContext
from runners.runner_base import PackRunnerBase
from workspace import WorkspaceStore

try:
    from orchestrator_tools import stream
except ImportError:  # pragma: no cover

    def stream(message_type: str, content: str, **metadata):  # type: ignore[misc]
        del message_type, metadata
        if content:
            print(content, flush=True)


class BinaryRunner(PackRunnerBase):
    """Run a native CLI once per resolved command (all in-scope targets by default)."""

    def __init__(
        self,
        binary: str,
        *,
        default_for_target: Callable[[str], str] | None = None,
        timeout: int = 300,
        context: PackContext | None = None,
        resolver: NativeBinaryResolver | None = None,
        store: WorkspaceStore | None = None,
    ) -> None:
        super().__init__(context)
        self.binary = binary
        self.default_for_target = default_for_target
        self.timeout = timeout
        self.resolver = resolver or NativeBinaryResolver()
        self.store = store or WorkspaceStore(self.context)

    def run(self) -> int:
        commands = self.context.resolve_commands(
            default_for_target=self.default_for_target
        )
        if not commands:
            print(
                f"usage: run.py '<{self.binary} …>'  "
                "(or set ORCHESTRATOR_ROLE_COMMAND / ORCHESTRATOR_IN_SCOPE)",
                file=sys.stderr,
            )
            return 2
        resolved = self.resolver.resolve(self.binary)
        if not resolved:
            msg = (
                f"{self.binary} not on PATH (or only a Python wrapper was found) — "
                "provision via tools/catalog before running this role"
            )
            stream("error", msg)
            print(msg, file=sys.stderr)
            return 1

        out_chunks: list[str] = []
        err_chunks: list[str] = []
        argv_log: list[str] = []
        worst = 0
        for cmd in commands:
            try:
                tokens = shlex.split(cmd)
            except ValueError as exc:
                print(f"bad command: {exc}", file=sys.stderr)
                return 2
            if not tokens or Path(tokens[0]).name != self.binary:
                print(f"command must start with {self.binary}", file=sys.stderr)
                return 2
            argv = [resolved, *tokens[1:]]
            argv_log.append(" ".join(argv))
            stream("log", f"running: {' '.join(argv)}")
            try:
                proc = subprocess.run(
                    argv, capture_output=True, text=True, timeout=self.timeout
                )
            except subprocess.TimeoutExpired:
                stream("error", f"{self.binary} timed out")
                print(f"{self.binary} timed out", file=sys.stderr)
                return 124
            if proc.stdout:
                stream("stdout", proc.stdout.rstrip("\n"))
                print(proc.stdout, end="" if proc.stdout.endswith("\n") else "\n")
                out_chunks.append(proc.stdout.rstrip("\n"))
            if proc.stderr:
                stream("stderr", proc.stderr.rstrip("\n"))
                print(
                    proc.stderr,
                    end="" if proc.stderr.endswith("\n") else "\n",
                    file=sys.stderr,
                )
                err_chunks.append(proc.stderr.rstrip("\n"))
            code = proc.returncode or 0
            if code and (worst == 0 or code > worst):
                worst = code

        role = self.context.role_name()
        # Skip ops persist for the reporting bookend role.
        skip_persist = False
        try:
            from orchestrator.crew.roles.registry import analyzer_role

            ana = analyzer_role()
            skip_persist = bool(ana and role == ana.id)
        except Exception:
            skip_persist = role in {"analyzer"}
        if role and not skip_persist:
            self.store.persist_role_run(
                role=role,
                binary=self.binary,
                argv=argv_log,
                stdout="\n".join(out_chunks),
                stderr="\n".join(err_chunks),
                code=worst,
            )
            # Do NOT record_finding here — run completion is ops status, not an
            # engagement finding. Roles/agents call record_finding for real discoveries.
        return worst
