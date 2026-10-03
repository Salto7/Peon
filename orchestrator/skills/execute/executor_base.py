"""Skill executor ABC and run DTOs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SkillRunRequest:
    skill_name: str
    script: str
    command: str = ""
    argv: list[str] = field(default_factory=list)


@dataclass
class SkillRunResult:
    ok: bool
    output: str
    exit_code: int | None = None
    script_path: Path | None = None


class SkillExecutorBase(ABC):
    @abstractmethod
    def run(self, request: SkillRunRequest) -> SkillRunResult: ...

    def supports(self, script_name: str) -> bool:
        return True
