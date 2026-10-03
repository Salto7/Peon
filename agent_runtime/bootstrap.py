"""Register built-in backends. Called by the control plane, not the orchestrator."""

from __future__ import annotations

_DONE = False


def register_builtin() -> None:
    global _DONE
    if _DONE:
        return
    from agent_runtime.docker.runtime import DockerRuntime
    from agent_runtime.openshell.runtime import OpenShellRuntime
    from agent_runtime.registry import register

    register(DockerRuntime())
    register(OpenShellRuntime())
    _DONE = True
