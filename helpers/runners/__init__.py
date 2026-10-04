"""Pack runners — class hierarchy behind thin ``assets/run.py`` entrypoints."""

from __future__ import annotations

from collections.abc import Callable

from runners.binary import BinaryRunner
from runners.cli import CliRunner
from runners.glue import GlueRunner
from runners.runner_base import PackRunnerBase

__all__ = [
    "BinaryRunner",
    "CliRunner",
    "GlueRunner",
    "PackRunnerBase",
    "main",
]


def main(fn: Callable[[], int]) -> None:
    """``if __name__ == "__main__": main(_run)``."""
    raise SystemExit(fn())
