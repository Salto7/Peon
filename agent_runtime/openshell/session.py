"""Run commands inside an OpenShell sandbox."""

from __future__ import annotations

from agent_runtime.api import ExecResult, SandboxSession, SessionInfo
from agent_runtime.openshell.cli import OpenShellCli
from agent_runtime.process import ExecTransport, HostProcessRunner
from agent_runtime.util import forward_env_items


class OpenShellSession(SandboxSession):
    def __init__(
        self,
        info: SessionInfo,
        *,
        cli: OpenShellCli | None = None,
        base_env: dict[str, str] | None = None,
    ) -> None:
        # "SANDBOX_HTTP_PROXY ignored on OpenShell".
        super().__init__(info)
        self._cli = cli or OpenShellCli.shared()
        self._base_env = {
            str(k): str(v) for k, v in (base_env or {}).items() if k and v is not None
        }

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
        remote = ExecTransport.command_in(cmd, cwd=work, shell=shell)
        env_prefix = [f"{k}={v}" for k, v in forward_env_items(self.env_get)]
        # Match DockerSession so skill scripts see the same mount layout.
        env_prefix.extend(
            [
                f"ORCHESTRATOR_WORKSPACE={workspace}",
                "SKILLS_DIR=/skills",
                "TOOLS_CATALOG_DIR=/tools/catalog",
                "PROJECT_WORKSPACES_DIR=/workspace",
                "PYTHONPATH=/skills/helpers",
            ]
        )
        for key, val in self._base_env.items():
            env_prefix.append(f"{key}={val}")
        for key, val in (env or {}).items():
            if key and val is not None:
                env_prefix.append(f"{key}={val}")
        if env_prefix:
            remote = ["env", *env_prefix, *remote]
        argv = [
            self._cli.require_bin(),
            "sandbox",
            "exec",
            "-n",
            self.info.name,
            "--",
            *remote,
        ]
        return HostProcessRunner.run(argv, timeout=timeout, shell=False, stdin=stdin)
