"""Sandbox package — backends, Docker CLI, session binding."""

from orchestrator.sandbox.backend import (
    BaseCommandCache,
    ExecResult,
    SandboxBackend,
    SandboxInfo,
    UnboundSandbox,
    run_process,
)
from orchestrator.sandbox.cli import DockerCli
from orchestrator.sandbox.docker import DockerSandbox
from orchestrator.sandbox.session import SandboxSession

__all__ = [
    "BaseCommandCache",
    "DockerCli",
    "DockerSandbox",
    "ExecResult",
    "SandboxBackend",
    "SandboxInfo",
    "SandboxSession",
    "UnboundSandbox",
    "run_process",
]
