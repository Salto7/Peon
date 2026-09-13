"""Skill executors: ABC, DTOs, Null refuse, runtime-shell, Local Docker runner."""

from __future__ import annotations

import os
import shlex
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.sandbox import SandboxSession
from orchestrator.skills.misc.registry import SkillRegistry
from orchestrator.skills.misc.utils import resolve_resource
from orchestrator.utils.service import SharedService

_TIMEOUT = 300
_DOCKER_MODES = frozenset({"docker", "shared"})


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


class SkillExecutor(ABC):
    @abstractmethod
    def run(self, request: SkillRunRequest) -> SkillRunResult: ...

    def supports(self, script_name: str) -> bool:
        return True


class NullSkillExecutor(SharedService, SkillExecutor):
    def run(self, request: SkillRunRequest) -> SkillRunResult:
        return SkillRunResult(
            ok=False,
            output=(
                f"Execution disabled: refused {request.skill_name}/{request.script}. "
                "Register LocalSkillExecutor (Docker sandbox) before running skills."
            ),
            exit_code=1,
        )


def absolutize_relative_paths(command: str, workdir: Path) -> str:
    """Rewrite relative path-like tokens to absolute paths under workdir."""
    if not command:
        return command
    try:
        tokens = shlex.split(command)
    except ValueError:
        return command
    root = workdir.resolve()
    out: list[str] = []
    changed = False
    for token in tokens:
        raw = token.strip().strip("'\"")
        path = Path(raw)
        if (
            not raw
            or raw.startswith("-")
            or path.is_absolute()
            or ".." in path.parts
            or len(path.parts) < 2
        ):
            out.append(token)
            continue
        target = (root / path).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            out.append(token)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        out.append(shlex.quote(str(target)))
        changed = True
    return " ".join(out) if changed else command


class LocalSkillExecutor(SharedService, SkillExecutor):
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

        backend = SandboxSession.current()
        if backend.info.mode not in _DOCKER_MODES:
            return SkillRunResult(
                ok=False,
                output=(
                    f"Host execution disabled — bind a Docker sandbox "
                    f"(got mode={backend.info.mode!r})"
                ),
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
        container_py = f"/skills/{skill_name}/scripts{os.pathsep}/skills/helpers"
        argv = ["env", f"PYTHONPATH={container_py}", "python3", script_in]
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
