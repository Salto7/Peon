"""Resolve scripts/ paths; dispatch to registered executors."""

from __future__ import annotations

from pathlib import Path

from orchestrator.skills.execute.executors import (
    NullSkillExecutor,
    SkillExecutor,
    SkillRunRequest,
    SkillRunResult,
)
from orchestrator.skills.misc.registry import SkillRegistry
from orchestrator.skills.misc.utils import resolve_resource
from orchestrator.utils.service import SharedService


class SkillExecutionDispatcher(SharedService):
    def __init__(
        self,
        *,
        registry: SkillRegistry | None = None,
        executors: list[SkillExecutor] | None = None,
    ) -> None:
        self._registry = registry or SkillRegistry.shared()
        # Default: refuse until LocalSkillExecutor is registered by the worker.
        self._executors = (
            list(executors) if executors is not None else [NullSkillExecutor.shared()]
        )

    def register(self, executor: SkillExecutor) -> None:
        self._executors.insert(0, executor)

    def resolve_script(self, skill_name: str, script: str) -> tuple[object | None, Path]:
        skill = self._registry.load_skill(skill_name)
        if not skill:
            raise ValueError(f"Skill not found: {skill_name}")
        path = resolve_resource(skill.skill_dir, script)
        if not path:
            raise ValueError(f"Script not found: {script} (skill={skill_name})")
        return skill, path

    def run(self, request: SkillRunRequest) -> SkillRunResult:
        try:
            _, path = self.resolve_script(request.skill_name, request.script)
        except ValueError as exc:
            return SkillRunResult(ok=False, output=str(exc), exit_code=1)

        for executor in self._executors:
            if executor.supports(path.name):
                result = executor.run(request)
                result.script_path = result.script_path or path
                return result
        return SkillRunResult(
            ok=False,
            output=f"No executor for: {request.script}",
            exit_code=1,
            script_path=path,
        )
