"""Project runtime lifecycle. Policy stays here; commands stay in agent_runtime.

- ``SANDBOX_ENABLED=true``  → one dedicated environment per Project
- ``SANDBOX_ENABLED=false`` → one shared Docker sandbox (OpenShell stays dedicated)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from django.conf import settings

from agent_runtime.api import Runtime, RuntimeSpec, Session, SessionInfo
from agent_runtime.bootstrap import register_builtin
from agent_runtime.registry import get as get_runtime
from orchestrator.utils.service import SharedService
from peon.projects.models import SandboxRuntime

logger = logging.getLogger(__name__)


def ensure_runtimes() -> None:
    register_builtin()


def runtime_id_for(project_id: str) -> str:
    pid = str(project_id or "").strip()
    if not pid:
        return SandboxRuntime.SANDBOX
    try:
        from peon.projects.models import Project

        value = (
            Project.objects.filter(pk=pid)
            .values_list("sandbox_runtime", flat=True)
            .first()
        )
    except Exception:
        return SandboxRuntime.SANDBOX
    return SandboxRuntime.resolve(value)


class ProjectSandbox(SharedService):
    """Choose the project's runtime and bind a session. Does not run commands."""

    def per_project(self) -> bool:
        return bool(getattr(settings, "SANDBOX_ENABLED", True))

    def prefix(self) -> str:
        return str(
            getattr(settings, "PROJECT_SANDBOX_PREFIX", "peon-project") or "peon-project"
        ).strip()

    def image_for(self, runtime_id: str) -> str:
        if runtime_id == SandboxRuntime.OPENSHELL:
            raw = str(getattr(settings, "OPENSHELL_IMAGE", "") or "").strip()
            if raw:
                return raw
        return str(
            getattr(settings, "SANDBOX_IMAGE", "peon-sandbox:local") or "peon-sandbox:local"
        )

    def runtime_for(self, project_id: str) -> Runtime:
        ensure_runtimes()
        return get_runtime(runtime_id_for(project_id))

    def uses_shared(self, project_id: str) -> bool:
        return (not self.per_project()) and runtime_id_for(project_id) == SandboxRuntime.SANDBOX

    @classmethod
    def container_name(cls, project_id: str) -> str:
        self = cls.shared()
        pid = str(project_id or "").strip()
        runtime = self.runtime_for(pid)
        return runtime.resource_name(
            pid,
            prefix=self.prefix(),
            shared=self.uses_shared(pid),
        )

    @classmethod
    def dedicated_name(cls, project_id: str) -> str:
        self = cls.shared()
        ensure_runtimes()
        runtime = get_runtime(runtime_id_for(project_id))
        return runtime.resource_name(str(project_id or ""), prefix=self.prefix(), shared=False)

    @classmethod
    def provision(
        cls,
        project_id: str,
        *,
        workspace: Path | None = None,
        skills_dir: Path | None = None,
        tools_dir: Path | None = None,
    ) -> SessionInfo:
        self = cls.shared()
        pid = str(project_id or "").strip()
        runtime = self.runtime_for(pid)
        spec = self._project_spec(
            pid,
            runtime_id=runtime.id,
            workspace=workspace,
            skills_dir=skills_dir,
            tools_dir=tools_dir,
        )
        session = runtime.provision(spec)
        _bind_session(session)
        return session.info

    @classmethod
    def remove(cls, project_id: str) -> dict[str, Any]:
        self = cls.shared()
        pid = str(project_id or "").strip()
        if not pid:
            return {"action": "skipped", "reason": "no project_id"}

        runtime = self.runtime_for(pid)
        shared_name = get_runtime(SandboxRuntime.SANDBOX).resource_name(
            "", prefix=self.prefix(), shared=True
        )
        dedicated = runtime.resource_name(pid, prefix=self.prefix(), shared=False)
        removed: list[str] = []
        errors: list[str] = []

        def _rm(target: str, *, role: str) -> None:
            if target == shared_name:
                return
            result = runtime.destroy(
                RuntimeSpec(project_id=pid, name=target, role=role, image=self.image_for(runtime.id))
            )
            if result.get("removed"):
                removed.append(target)
                return
            err = str(result.get("error") or "")
            if err:
                errors.append(f"{target}: {err}")

        try:
            _rm(dedicated, role="project")
        except Exception as exc:
            errors.append(f"{dedicated}: {exc}")

        try:
            for cid in runtime.find(f"label=peon.project_id={pid}"):
                if cid in removed or cid == dedicated or cid == shared_name:
                    continue
                try:
                    _rm(cid, role="project")
                except Exception as exc:
                    errors.append(f"{cid}: {exc}")
        except Exception as exc:
            errors.append(str(exc))

        Session.reset()
        if removed:
            return {
                "action": "removed",
                "name": dedicated,
                "removed": removed,
                "errors": errors,
            }
        if errors:
            return {"action": "error", "name": dedicated, "errors": errors}
        return {"action": "missing", "name": dedicated}

    def _project_spec(
        self,
        project_id: str,
        *,
        runtime_id: str,
        workspace: Path | None,
        skills_dir: Path | None,
        tools_dir: Path | None,
    ) -> RuntimeSpec:
        ws = Path(workspace or "/tmp").resolve()
        ws.mkdir(parents=True, exist_ok=True)
        skills, tools = self._skills_tools(skills_dir, tools_dir)
        shared = self.uses_shared(project_id) and runtime_id == SandboxRuntime.SANDBOX
        if shared:
            root = Path(getattr(settings, "PROJECT_WORKSPACES_DIR", ws.parent)).resolve()
            try:
                rel = ws.resolve().relative_to(root).as_posix()
            except ValueError:
                rel = ws.name
            mount = root
            workdir = f"/workspace/{rel}" if rel else "/workspace"
            role = "shared"
            name = self.runtime_for(project_id).resource_name(
                project_id, prefix=self.prefix(), shared=True
            )
            labels = {"peon.role": "shared-sandbox"}
        else:
            mount = ws
            workdir = "/sandbox" if runtime_id == SandboxRuntime.OPENSHELL else "/workspace"
            role = "project"
            name = self.runtime_for(project_id).resource_name(
                project_id, prefix=self.prefix(), shared=False
            )
            labels = {"peon.role": "project-sandbox", "peon.project_id": project_id}

        sock = str(getattr(settings, "STREAM_SOCKET_PATH", "") or "")
        volume = str(getattr(settings, "SANDBOX_SOCKETS_VOLUME", "") or "").strip()
        state = Path(getattr(settings, "PROJECT_WORKSPACES_DIR", Path.cwd() / "data")).resolve().parent / "runtime"
        return RuntimeSpec(
            project_id=project_id,
            name=name,
            image=self.image_for(runtime_id),
            role=role,
            workspace_host=str(mount),
            skills_host=str(skills),
            tools_host=str(tools),
            socket_dir_host="" if volume else (str(Path(sock).parent) if sock else ""),
            socket_volume=volume,
            container_workdir=workdir,
            labels=labels,
            state_dir=str(state),
        )

    def _skills_tools(
        self, skills_dir: Path | None, tools_dir: Path | None
    ) -> tuple[Path, Path]:
        skills = Path(skills_dir or getattr(settings, "SKILLS_DIR", "skills")).resolve()
        tools = Path(
            tools_dir or getattr(settings, "TOOLS_CATALOG_DIR", "tools/catalog")
        ).resolve()
        return skills, tools


def _bind_session(session) -> None:
    from orchestrator.utils.job_env import JobEnv

    work = (session.info.workdir or "/workspace").strip() or "/workspace"
    os.environ["ORCHESTRATOR_SANDBOX_WORKDIR"] = work
    if JobEnv.current():
        JobEnv.bind({**JobEnv.current(), "ORCHESTRATOR_SANDBOX_WORKDIR": work})
    session.set_env_lookup(JobEnv.get)
    Session.bind(session)
