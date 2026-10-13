"""Run commands inside a Docker container. This is the sandbox exec path."""

from __future__ import annotations

from agent_runtime.api import ExecResult, RuntimeSession, SessionInfo
from agent_runtime.docker.cli import DockerCli
from agent_runtime.process import ExecTransport, HostProcessRunner
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
        env: dict[str, str] | None = None,
        stdin: str | None = None,
    ) -> ExecResult:
        work = cwd or self.workdir()
        workspace = self.workdir()
        argv: list[str] = [self._docker, "exec"]
        if stdin is not None:
            argv.append("-i")
        argv.extend(
            [
            "-w",
            work,
            "-e",
            "DEBIAN_FRONTEND=noninteractive",
            "-e",
            f"ORCHESTRATOR_WORKSPACE={workspace}",
            "-e",
            "SKILLS_DIR=/skills",
            "-e",
            "SKILLS_IMPORTED_DIR=/skills-imported",
            "-e",
            "HELPERS_DIR=/helpers",
            "-e",
            "TOOLS_CATALOG_DIR=/tools/catalog",
            "-e",
            "PROJECT_WORKSPACES_DIR=/workspace",
            "-e",
            # Include sbin paths — Kali (and Debian) put many admin CLIs there.
            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "-e",
            "PYTHONPATH=/skills/helpers:/helpers",
            ]
        )
        for key, val in forward_env_items(
            self.env_get,
            skip={"ORCHESTRATOR_WORKSPACE", "ORCHESTRATOR_SANDBOX_WORKDIR"},
        ):
            argv.extend(["-e", f"{key}={val}"])
        for key, val in (env or {}).items():
            if key and val is not None:
                argv.extend(["-e", f"{key}={val}"])
        argv.append(self.info.name)
        argv.extend(ExecTransport.command(cmd, shell=shell))
        return HostProcessRunner.run(argv, timeout=timeout, shell=False, stdin=stdin)
