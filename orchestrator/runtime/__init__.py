"""Core runtime exports: install resolve, shell runner, host RPC bridge."""

from orchestrator.runtime.shell import ProvisionService, ShellRunner, StreamEmitter

__all__ = [
    "InstallResolver",
    "ProvisionService",
    "RpcServer",
    "ShellRunner",
    "StreamEmitter",
]


def __getattr__(name: str):
    if name == "InstallResolver":
        from orchestrator.runtime.resolve import InstallResolver

        return InstallResolver
    if name == "RpcServer":
        from orchestrator.runtime.rpc import RpcServer

        return RpcServer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
