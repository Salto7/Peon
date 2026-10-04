"""PackRunnerBase — one role-pack execution mode."""

from __future__ import annotations

from abc import ABC, abstractmethod

from context import PackContext


class PackRunnerBase(ABC):
    """One pack execution mode (binary / CLI / glue)."""

    def __init__(self, context: PackContext | None = None) -> None:
        self.context = context or PackContext()

    @abstractmethod
    def run(self) -> int:
        """Execute and return a process-style exit code."""
