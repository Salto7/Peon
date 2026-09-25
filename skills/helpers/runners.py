"""Skill runners — class hierarchy behind thin ``scripts/run.py`` entrypoints."""

from __future__ import annotations

import shlex
import subprocess
import sys
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any

from binary import NativeBinaryResolver
from context import SkillContext
from workspace import WorkspaceStore

try:
    # deferred: optional dep (sandbox mounts orchestrator_tools; host may lack it)
    from orchestrator_tools import stream
except ImportError:  # pragma: no cover

    def stream(message_type: str, content: str, **metadata: Any) -> None:  # type: ignore[misc]
        del message_type, metadata
        if content:
            print(content, flush=True)


LLM_BODY_MARKER = "# --- LLM_BODY ---"


class SkillRunner(ABC):
    """One skill execution mode (binary / CLI / report / glue)."""

    def __init__(self, context: SkillContext | None = None) -> None:
        self.context = context or SkillContext()

    @abstractmethod
    def run(self) -> int:
        """Execute and return a process-style exit code."""


class BinaryRunner(SkillRunner):
    """Run a native CLI once per resolved command (all in-scope targets by default)."""

    def __init__(
        self,
        binary: str,
        *,
        default_for_target: Callable[[str], str] | None = None,
        timeout: int = 300,
        context: SkillContext | None = None,
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
                "(or set ORCHESTRATOR_SKILL_COMMAND / ORCHESTRATOR_IN_SCOPE)",
                file=sys.stderr,
            )
            return 2
        resolved = self.resolver.resolve(self.binary)
        if not resolved:
            msg = (
                f"{self.binary} not on PATH (or only a Python wrapper was found) — "
                "provision via tools/catalog before running this skill"
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

        skill = self.context.skill_name()
        if skill and skill != "analyzer":
            self.store.persist_skill_run(
                skill=skill,
                binary=self.binary,
                argv=argv_log,
                stdout="\n".join(out_chunks),
                stderr="\n".join(err_chunks),
                code=worst,
            )
            # Do NOT record_finding here — run completion is ops status, not an
            # engagement finding. Skills/agents call record_finding for real discoveries.
        return worst


class CliRunner(SkillRunner):
    """Shlex-split skill command and hand argv to a Python CLI handler."""

    def __init__(
        self,
        handler: Callable[[list[str]], int],
        *,
        usage: str = "",
        context: SkillContext | None = None,
    ) -> None:
        super().__init__(context)
        self.handler = handler
        self.usage = usage

    def run(self) -> int:
        raw = self.context.resolve_command()
        if not (raw or "").strip():
            print(self.usage or "usage: run.py '<subcommand and args>'", file=sys.stderr)
            return 2
        try:
            argv = shlex.split(raw)
        except ValueError as exc:
            print(f"bad command: {exc}", file=sys.stderr)
            return 2
        return int(self.handler(argv))


class ReportRunner(SkillRunner):
    """Build findings/report.md via a workspace builder callback."""

    def __init__(
        self,
        builder: Callable[[Path], str | Path],
        *,
        context: SkillContext | None = None,
        store: WorkspaceStore | None = None,
    ) -> None:
        super().__init__(context)
        self.builder = builder
        self.store = store or WorkspaceStore(self.context)

    def run(self) -> int:
        ws = self.store.path()
        findings = ws / "findings"
        findings.mkdir(parents=True, exist_ok=True)
        result = self.builder(ws)
        if isinstance(result, Path):
            print(f"Wrote {result}")
        else:
            report = findings / "report.md"
            report.write_text(str(result), encoding="utf-8")
            print(f"Wrote {report}")
        return 0


class GlueRunner(SkillRunner):
    """Execute the body below ``# --- LLM_BODY ---`` with optional imports."""

    def __init__(
        self,
        script_path: str | Path,
        *,
        imports: str = "",
        context: SkillContext | None = None,
    ) -> None:
        super().__init__(context)
        self.script_path = Path(script_path)
        self.imports = imports

    def run(self) -> int:
        text = self.script_path.read_text(encoding="utf-8")
        if LLM_BODY_MARKER not in text:
            print(
                f"missing {LLM_BODY_MARKER} in {self.script_path.name}",
                file=sys.stderr,
            )
            return 2
        body = text.split(LLM_BODY_MARKER, 1)[1].lstrip("\n")
        if not body.strip():
            print("empty LLM body — nothing to run", file=sys.stderr)
            return 2
        ns: dict[str, Any] = {
            "__name__": "__main__",
            "__file__": str(self.script_path),
        }
        if self.imports.strip():
            exec(self.imports, ns, ns)  # noqa: S102 — intentional skill glue surface
        exec(body, ns, ns)  # noqa: S102
        return 0


def main(fn: Callable[[], int]) -> None:
    """``if __name__ == "__main__": main(_run)``."""
    raise SystemExit(fn())
