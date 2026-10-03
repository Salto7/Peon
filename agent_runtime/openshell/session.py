"""Run commands inside an OpenShell sandbox."""

from __future__ import annotations

import shlex

from agent_runtime.api import ExecResult, RuntimeSession, SessionInfo
from agent_runtime.openshell.cli import OpenShellCli
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
    "ORCHESTRATOR_WORKSPACE",
    "ORCHESTRATOR_SANDBOX_WORKDIR",
)


class OpenShellSession(RuntimeSession):
    def __init__(self, info: SessionInfo, *, cli: OpenShellCli | None = None) -> None:
        super().__init__(info)
        self._cli = cli or OpenShellCli()

    def exec(
        self,
        cmd: list[str] | str,
        *,
        timeout: float | None = 300,
        shell: bool = False,
        cwd: str | None = None,
    ) -> ExecResult:
        work = cwd or self.workdir()
        if shell or isinstance(cmd, str):
            inner = cmd if isinstance(cmd, str) else " ".join(cmd)
            remote = ["bash", "-lc", f"cd {shlex.quote(work)} && {inner}"]
        else:
            remote = ["bash", "-lc", "cd " + shlex.quote(work) + " && " + " ".join(shlex.quote(c) for c in cmd)]
        env_prefix: list[str] = []
        for key in _FORWARD_ENV:
            val = self.env_get(key)
            if val:
                env_prefix.extend([f"{key}={val}"])
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
        return run_process(argv, timeout=timeout, shell=False)

    def which(self, binary: str) -> bool:
        if not binary:
            return False
        return self.exec(
            ["sh", "-c", f"command -v {shlex.quote(binary)}"],
            timeout=15,
        ).ok
