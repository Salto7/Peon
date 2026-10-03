"""Run skill scripts via the bound agent runtime session."""

from __future__ import annotations

import os
from pathlib import Path

from agent_runtime.api import Session
from orchestrator.skills.execute.executor_base import (
    SkillExecutorBase,
    SkillRunRequest,
    SkillRunResult,
)
from orchestrator.skills.common import resolve_resource
from orchestrator.skills.registry import SkillRegistry
from orchestrator.utils.paths import absolutize_relative_paths
from orchestrator.utils.service import SharedServiceBase

_TIMEOUT = 300


class LocalSkillExecutor(SharedServiceBase, SkillExecutorBase):
    """Run skill ``scripts/*.py`` via ``docker exec`` on the bound sandbox."""

    def supports(self, script_name: str) -> bool:
        return script_name.endswith(".py")

    def run(self, request: SkillRunRequest) -> SkillRunResult:
        skill = SkillRegistry.shared().load_skill(request.skill_name)
        if skill is None:
            return SkillRunResult(
                ok=False, output=f"Skill not found: {request.skill_name}", exit_code=1
            )
        path = resolve_resource(skill.skill_dir, request.script)
        if path is None:
            return SkillRunResult(
                ok=False,
                output=f"Script not found: {request.script} (skill={request.skill_name})",
                exit_code=1,
            )

        backend = Session.current()
        err = Session.require_bound()
        if err:
            return SkillRunResult(
                ok=False,
                output=err.replace("Error: ", "Host execution disabled — ", 1),
                exit_code=1,
                script_path=path,
            )

        from orchestrator.utils.job_env import JobEnv

        skill_dir = Path(skill.skill_dir)
        workdir = self._workdir(skill.skill_dir)
        workdir.mkdir(parents=True, exist_ok=True)
        command = absolutize_relative_paths(request.command or "", workdir)

        container_ws = backend.workdir()
        skill_name = skill_dir.name
        script_in = f"/skills/{skill_name}/{request.script}"
        helpers_path = "/skills/helpers"
        scripts_path = f"/skills/{skill_name}/scripts"
        container_py = (
            f"{helpers_path}{os.pathsep}{scripts_path}{os.pathsep}/orchestrator-src"
        )
        argv = [
            "env",
            f"PYTHONPATH={container_py}",
            "SKILLS_DIR=/skills",
            "ORCHESTRATOR_HELPERS_DIR=/skills/helpers",
            "python3",
            script_in,
        ]
        if request.argv:
            argv.extend(request.argv)
        elif command:
            remapped = command.replace(str(workdir), container_ws)
            argv.append(remapped)
        overlay = {
            "ORCHESTRATOR_SKILL_NAME": skill_name,
            "ORCHESTRATOR_SKILL_COMMAND": command,
        }
        try:
            with JobEnv.overlay(overlay):
                res = backend.exec(argv, timeout=_TIMEOUT, cwd=container_ws)
        except Exception as exc:
            return SkillRunResult(
                ok=False, output=str(exc), exit_code=1, script_path=path
            )
        out = res.stdout + (("\n" + res.stderr) if res.stderr else "")
        return SkillRunResult(
            ok=res.ok,
            output=out.strip() or f"exit={res.code}",
            exit_code=res.code,
            script_path=path,
        )

    @staticmethod
    def _workdir(skill_dir: str) -> Path:
        fallback = Path(skill_dir).resolve()
        from orchestrator.utils.job_env import JobEnv

        raw = JobEnv.get("ORCHESTRATOR_WORKSPACE")
        if not raw:
            return fallback
        candidate = Path(raw).resolve()
        root_raw = JobEnv.get("PROJECT_WORKSPACES_DIR")
        if root_raw:
            try:
                candidate.relative_to(Path(root_raw).resolve())
            except ValueError:
                return fallback
        return candidate
