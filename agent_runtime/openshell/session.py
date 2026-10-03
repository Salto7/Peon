"""Run commands inside an OpenShell sandbox."""

from __future__ import annotations

import shlex

from agent_runtime.api import ExecResult, RuntimeSession, SessionInfo
from agent_runtime.openshell.cli import OpenShellCli
from agent_runtime.process import run_process
from agent_runtime.util import forward_env_items


class OpenShellSession(RuntimeSession):
    def __init__(self, info: SessionInfo, *, cli: OpenShellCli | None = None) -> None:
        super().__init__(info)
        self._cli = cli or OpenShellCli.shared()

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
            remote = [
                "bash",
                "-lc",
                "cd "
                + shlex.quote(work)
                + " && "
                + " ".join(shlex.quote(c) for c in cmd),
            ]
        env_prefix = [f"{k}={v}" for k, v in forward_env_items(self.env_get)]
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
