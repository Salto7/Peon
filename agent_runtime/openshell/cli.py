"""OpenShell CLI. Sandbox create / exec / delete only."""

from __future__ import annotations

import shutil

from agent_runtime.api import ExecResult
from agent_runtime.process import run_process

_DEFAULT_POLICY = """version: 1
filesystem_policy:
  include_workdir: true
  read_only: [/usr, /lib, /lib64, /etc]
  read_write: [/tmp]
landlock:
  compatibility: best_effort
network_policies: {}
"""


def policy_text() -> str:
    """Default-deny network. The operator warning on the create form matches this."""
    return _DEFAULT_POLICY


class OpenShellCli:
    def bin(self) -> str | None:
        return shutil.which("openshell")

    def available(self) -> bool:
        return bool(self.bin())

    def require_bin(self) -> str:
        path = self.bin()
        if not path:
            raise RuntimeError(
                "openshell CLI missing — install OpenShell or create the project "
                "with the Sandbox runtime"
            )
        return path

    def run(self, args: list[str], *, timeout: float = 180) -> ExecResult:
        return run_process([self.require_bin(), *args], timeout=timeout)
