"""Run commands inside a Docker container. This is the sandbox exec path."""

from __future__ import annotations

import shlex

from agent_runtime.api import BaseCommandCache, ExecResult, RuntimeSession, SessionInfo
from agent_runtime.docker.cli import DockerCli
from agent_runtime.process import run_process

_FORWARD_ENV = (
    "ORCHESTRATOR_JOB_ID",
    "ORCHESTRATOR_STREAM_SOCKET",
    "ORCHESTRATOR_RPC_SOCKET",
    "ORCHESTRATOR_RPC_TOKEN",
    "ORCHESTRATOR_SKILL_COMMAND",
    "ORCHESTRATOR_SKILL_NAME",
    "ORCHESTRATOR_IN_SCOPE",
    "ORCHESTRATOR_EXCLUSIONS",
    "ORCHESTRATOR_SEED",
    "ORCHESTRATOR_PROJECT_ID",
    "ORCHESTRATOR_JOB_BRIEF",
)


class DockerSession(RuntimeSession):
    def __init__(self, info: SessionInfo, *, docker_bin: str | None = None) -> None:
        super().__init__(info)
        self._docker = docker_bin or DockerCli.shared().bin() or "docker"

    def workdir(self) -> str:
        return (
            self.env_get("ORCHESTRATOR_SANDBOX_WORKDIR")
            or (self.info.workdir or "").strip()
            or "/workspace"
        )

    def exec(
        self,
        cmd: list[str] | str,
        *,
        timeout: float | None = 300,
        shell: bool = False,
        cwd: str | None = None,
    ) -> ExecResult:
        work = cwd or self.workdir()
        argv: list[str] = [
            self._docker,
            "exec",
            "-w",
            work,
            "-e",
            "DEBIAN_FRONTEND=noninteractive",
            "-e",
            f"ORCHESTRATOR_WORKSPACE={work}",
            "-e",
            "SKILLS_DIR=/skills",
            "-e",
            "SANDBOX_SKILLS_PATH=/skills",
            "-e",
            "TOOLS_CATALOG_DIR=/tools/catalog",
            "-e",
            "PROJECT_WORKSPACES_DIR=/workspace",
            "-e",
            "PATH=/usr/local/bin:/usr/bin:/bin",
        ]
        for key in _FORWARD_ENV:
            val = self.env_get(key)
            if val:
                argv.extend(["-e", f"{key}={val}"])
        argv.append(self.info.name)
        if shell or isinstance(cmd, str):
            argv.extend(["bash", "-lc", cmd if isinstance(cmd, str) else " ".join(cmd)])
        else:
            argv.extend(cmd)
        return run_process(argv, timeout=timeout, shell=False)

    def which(self, binary: str) -> bool:
        if not binary:
            return False
        return self.exec(
            ["sh", "-c", f"command -v {shlex.quote(binary)}"],
            timeout=15,
        ).ok

    def load_base_commands(self) -> frozenset[str]:
        """Read and cache the image marker once per process."""
        cache = BaseCommandCache.shared()
        cached = cache.get(self.info.image or self.info.mode)
        if cached is not None:
            self._base = cached
            self.info.base_commands = cached
            return cached
        found = self.base_commands()
        self.info.base_commands = found
        return found
