"""Sandbox backends: process helpers, ABC, unbound fail-closed, base-cmd cache."""

from __future__ import annotations

import os
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from orchestrator.utils.service import SharedService

_BASE_CMDS_FILE = "/etc/peon/base-commands"


@dataclass(frozen=True)
class ExecResult:
    code: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.code == 0


def run_process(
    cmd: list[str] | str,
    *,
    timeout: float | None = 300,
    shell: bool = False,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> ExecResult:
    merged = {**os.environ, "DEBIAN_FRONTEND": "noninteractive", **(env or {})}
    try:
        proc = subprocess.run(
            cmd,
            shell=shell,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=merged,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        err = (
            exc.stderr
            if isinstance(exc.stderr, str)
            else f"timed out after {timeout}s"
        )
        return ExecResult(124, out, err)
    return ExecResult(proc.returncode or 0, proc.stdout or "", proc.stderr or "")


@dataclass
class SandboxInfo:
    project_id: str
    name: str
    mode: str  # local | docker | shared | unbound | learn-lab
    action: str = "existing"
    image: str = ""
    workdir: str = ""  # container cwd; empty → backend default (/workspace)
    base_commands: frozenset[str] = field(default_factory=frozenset)


class BaseCommandCache(SharedService):
    """One base-command set per sandbox image (not per project)."""

    def __init__(self) -> None:
        self._by_image: dict[str, frozenset[str]] = {}

    @staticmethod
    def marker_path() -> str:
        return _BASE_CMDS_FILE

    @staticmethod
    def key(image: str, *, mode: str = "local") -> str:
        return (image or mode or "local").strip() or "local"

    def get(self, image_key: str) -> frozenset[str] | None:
        return self._by_image.get(self.key(image_key))

    def put(self, image_key: str, commands: frozenset[str]) -> frozenset[str]:
        key = self.key(image_key)
        frozen = frozenset(commands)
        self._by_image[key] = frozen
        return frozen

    def clear(self) -> None:
        self._by_image.clear()

    @staticmethod
    def parse_marker(stdout: str) -> frozenset[str]:
        return frozenset(
            line.strip()
            for line in (stdout or "").splitlines()
            if line.strip() and not line.strip().startswith("#")
        )


class SandboxBackend(ABC):
    """Exec + PATH queries for one project sandbox."""

    def __init__(self, info: SandboxInfo) -> None:
        self.info = info
        self._base: frozenset[str] | None = None

    @abstractmethod
    def exec(
        self,
        cmd: list[str] | str,
        *,
        timeout: float | None = 300,
        shell: bool = False,
        cwd: str | None = None,
    ) -> ExecResult: ...

    @abstractmethod
    def which(self, binary: str) -> bool: ...

    def workdir(self) -> str:
        from orchestrator.utils.job_env import JobEnv

        return JobEnv.get("ORCHESTRATOR_SANDBOX_WORKDIR") or (
            JobEnv.get("ORCHESTRATOR_WORKSPACE") or "."
        )

    def base_commands(self) -> frozenset[str]:
        if self._base is not None:
            return self._base
        if self.info.base_commands:
            self._base = self.info.base_commands
            return self._base
        cache = BaseCommandCache.shared()
        key = cache.key(self.info.image, mode=self.info.mode)
        cached = cache.get(key)
        if cached is not None:
            self._base = cached
            return self._base
        discovered = self._read_base_commands()
        self._base = cache.put(key, discovered)
        return self._base

    def _read_base_commands(self) -> frozenset[str]:
        path = BaseCommandCache.marker_path()
        res = self.exec(
            f"if [ -f {path} ]; then cat {path}; fi",
            shell=True,
            timeout=30,
        )
        return BaseCommandCache.parse_marker(res.stdout)


class UnboundSandbox(SandboxBackend):
    """Refuse exec until a Docker sandbox is bound."""

    def exec(
        self,
        cmd: list[str] | str,
        *,
        timeout: float | None = 300,
        shell: bool = False,
        cwd: str | None = None,
    ) -> ExecResult:
        raise RuntimeError(
            "No Docker sandbox bound — provision a per-project or shared sandbox first"
        )

    def which(self, binary: str) -> bool:
        return False

    def _read_base_commands(self) -> frozenset[str]:
        return frozenset()
