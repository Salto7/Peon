"""Public entry surface for role ``assets/run.py``."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from binary import NativeBinaryResolver
from context import PackContext
from runners import BinaryRunner, CliRunner, GlueRunner, main
from workspace import WorkspaceStore

__all__ = [
    "main",
    "run_binary",
    "run_cli",
    "run_glue",
    "workspace_path",
]

_ctx = PackContext()
_resolver = NativeBinaryResolver()
_store = WorkspaceStore(_ctx)


def workspace_path() -> Path:
    return _store.path()


def run_binary(
    binary: str,
    *,
    default_for_target: Callable[[str], str] | None = None,
    timeout: int = 300,
) -> int:
    return BinaryRunner(
        binary,
        default_for_target=default_for_target,
        timeout=timeout,
        context=_ctx,
        resolver=_resolver,
        store=_store,
    ).run()


def run_cli(
    handler: Callable[[list[str]], int],
    *,
    usage: str = "",
) -> int:
    return CliRunner(handler, usage=usage, context=_ctx).run()


def run_glue(script_path: str | Path, *, imports: str = "") -> int:
    return GlueRunner(script_path, imports=imports, context=_ctx).run()
