"""Core runtime exports: install resolve + shell runner."""

from orchestrator.runtime.shell import ProvisionService, ShellRunner, StreamEmitter

__all__ = [
    "InstallResolver",
    "ProvisionService",
    "ShellRunner",
    "StreamEmitter",
]


def __getattr__(name: str):
    if name == "InstallResolver":
        from orchestrator.runtime.resolve import InstallResolver

        return InstallResolver
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
