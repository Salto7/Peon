"""Docker-backed project sandbox."""

from __future__ import annotations

import shlex

from orchestrator.sandbox.backend import (
    ExecResult,
    SandboxBackend,
    SandboxInfo,
    run_process,
)
from orchestrator.sandbox.cli import DockerCli
from orchestrator.utils.job_env import JobEnv

# Job-scoped keys forwarded into the container (from JobEnv, not process globals).
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


class DockerSandbox(SandboxBackend):
    """Commands run inside ``docker exec`` for a project container."""

    def __init__(self, info: SandboxInfo, *, docker_bin: str | None = None) -> None:
        super().__init__(info)
        self._docker = docker_bin or DockerCli.shared().bin() or "docker"

    def workdir(self) -> str:
        return (
            JobEnv.get("ORCHESTRATOR_SANDBOX_WORKDIR")
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
            val = JobEnv.get(key)
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
            ["sh", "-c", f"command -v {shlex.quote(binary)}"], timeout=15
        ).ok
