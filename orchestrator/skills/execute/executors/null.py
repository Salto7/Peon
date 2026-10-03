"""Refuse skill script runs until a real executor is registered."""

from __future__ import annotations

from orchestrator.skills.execute.executor_base import (
    SkillExecutorBase,
    SkillRunRequest,
    SkillRunResult,
)
from orchestrator.utils.service import SharedServiceBase


class NullSkillExecutor(SharedServiceBase, SkillExecutorBase):
    def run(self, request: SkillRunRequest) -> SkillRunResult:
        return SkillRunResult(
            ok=False,
            output=(
                f"Execution disabled: refused {request.skill_name}/{request.script}. "
                "Register LocalSkillExecutor (Docker sandbox) before running skills."
            ),
            exit_code=1,
        )
