"""Run commands inside a Docker container. This is the sandbox exec path."""

from __future__ import annotations

from agent_runtime.api import ExecResult, RuntimeSession, SessionInfo
from agent_runtime.docker.cli import DockerCli
from agent_runtime.process import run_process
from agent_runtime.util import forward_env_items


class DockerSession(RuntimeSession):
    def __init__(self, info: SessionInfo, *, docker_bin: str | None = None) -> None:
        super().__init__(info)
        self._docker = docker_bin or DockerCli.shared().bin() or "docker"

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
            "ROLES_DIR=/roles",
            "-e",
            "SANDBOX_ROLES_PATH=/roles",
            "-e",
            "HELPERS_DIR=/helpers",
            "-e",
            "TOOLS_CATALOG_DIR=/tools/catalog",
            "-e",
            "PROJECT_WORKSPACES_DIR=/workspace",
            "-e",
            # Include sbin paths — Kali (and Debian) put many admin CLIs there.
            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        ]
        for key, val in forward_env_items(
            self.env_get,
            skip={"ORCHESTRATOR_WORKSPACE", "ORCHESTRATOR_SANDBOX_WORKDIR"},
        ):
            argv.extend(["-e", f"{key}={val}"])
        argv.append(self.info.name)
        if shell or isinstance(cmd, str):
            argv.extend(["bash", "-lc", cmd if isinstance(cmd, str) else " ".join(cmd)])
        else:
            argv.extend(cmd)
        return run_process(argv, timeout=timeout, shell=False)
