"""Per-project or shared Docker sandbox lifecycle (provision / bind / remove).

Peon control-plane policy around ``orchestrator.sandbox.DockerCli``.
Skill scripts never run on the host — only via ``docker exec``.

- ``SANDBOX_ENABLED=true``  → one dedicated container per Project
- ``SANDBOX_ENABLED=false`` → one shared container for all projects
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from django.conf import settings

from orchestrator.sandbox import (
    BaseCommandCache,
    DockerCli,
    DockerSandbox,
    SandboxInfo,
    SandboxSession,
)
from orchestrator.utils.service import SharedService

logger = logging.getLogger(__name__)


class ProjectSandbox(SharedService):
    """Provision / remove Docker sandboxes and bind the backend for the worker."""

    def per_project(self) -> bool:
        """Dedicated container per project when SANDBOX_ENABLED; else shared."""
        return bool(getattr(settings, "SANDBOX_ENABLED", True))

    def enabled(self) -> bool:
        """Docker sandboxes are always used (per-project or shared)."""
        return True

    def shared_container_name(self) -> str:
        prefix = str(
            getattr(settings, "PROJECT_SANDBOX_PREFIX", "peon-project") or "peon-project"
        ).strip()
        return f"{prefix}-shared"

    @classmethod
    def container_name(cls, project_id: str) -> str:
        self = cls.shared()
        if not self.per_project():
            return self.shared_container_name()
        return self.dedicated_name(project_id)

    @classmethod
    def dedicated_name(cls, project_id: str) -> str:
        """Per-project container name (even when shared mode is active)."""
        prefix = str(
            getattr(settings, "PROJECT_SANDBOX_PREFIX", "peon-project") or "peon-project"
        ).strip()
        safe = DockerCli.sanitize_name_fragment(project_id)
        budget = max(8, 63 - len(prefix) - 1)
        return f"{prefix}-{safe[:budget]}"

    def image(self) -> str:
        return str(
            getattr(settings, "SANDBOX_IMAGE", "peon-sandbox:local") or "peon-sandbox:local"
        )

    def _cli(self) -> DockerCli:
        return DockerCli.shared()

    @classmethod
    def provision(
        cls,
        project_id: str,
        *,
        workspace: Path | None = None,
        skills_dir: Path | None = None,
        tools_dir: Path | None = None,
    ) -> SandboxInfo:
        self = cls.shared()
        pid = str(project_id or "").strip()
        cli = self._cli()
        if not cli.available():
            raise RuntimeError(
                "docker CLI missing — skill execution requires a Docker sandbox "
                "(per-project or shared); host execution is disabled"
            )

        image = self.image()
        ws = Path(workspace or "/tmp").resolve()
        ws.mkdir(parents=True, exist_ok=True)

        if self.per_project() and pid:
            name = cls.container_name(pid)
            mode = "docker"
            volume_args, container_ws = self._volume_args_per_project(
                ws, skills_dir, tools_dir
            )
            labels = [
                "peon.role=project-sandbox",
                f"peon.project_id={pid}",
            ]
        else:
            name = self.shared_container_name()
            mode = "shared"
            volume_args, container_ws = self._volume_args_shared(
                ws, skills_dir, tools_dir
            )
            labels = ["peon.role=shared-sandbox"]

        action = cli.ensure_running(
            name,
            image=image,
            workdir=container_ws,
            labels=labels,
            volume_args=volume_args,
        )

        os.environ["ORCHESTRATOR_SANDBOX_WORKDIR"] = container_ws
        from orchestrator.utils.job_env import JobEnv

        # Keep workdir job-local when a JobEnv is already bound (parallel-safe).
        if JobEnv.current():
            JobEnv.bind({**JobEnv.current(), "ORCHESTRATOR_SANDBOX_WORKDIR": container_ws})
        cache = BaseCommandCache.shared()
        base = cache.get(image)
        info = SandboxInfo(
            project_id=pid,
            name=name,
            mode=mode,
            action=action,
            image=image,
            workdir=container_ws,
            base_commands=base or frozenset(),
        )
        backend = DockerSandbox(info, docker_bin=cli.require_bin())
        if base is None:
            base = backend.base_commands()
            cache.put(image, base)
            info = SandboxInfo(
                project_id=pid,
                name=name,
                mode=mode,
                action=action,
                image=image,
                workdir=container_ws,
                base_commands=base,
            )
            backend.info = info
        SandboxSession.bind(backend)
        return info

    @classmethod
    def remove(cls, project_id: str) -> dict[str, Any]:
        """Force-remove the project sandbox container(s).

        Shared-mode: never remove the shared container on project delete.
        Always attempts per-project cleanup when docker exists (orphans from prior runs).
        """
        self = cls.shared()
        pid = str(project_id or "").strip()
        if not pid:
            return {"action": "skipped", "reason": "no project_id"}

        cli = self._cli()
        shared = self.shared_container_name()
        # Per-project name even when currently in shared mode (orphan cleanup).
        dedicated = self.dedicated_name(pid)

        if not cli.available():
            logger.info("docker CLI missing; skip sandbox remove for %s", dedicated)
            return {
                "action": "skipped",
                "reason": "docker unavailable",
                "name": dedicated,
            }

        removed: list[str] = []
        errors: list[str] = []

        def _rm(target: str) -> None:
            if target == shared:
                return  # never destroy the shared sandbox on project delete
            proc = cli.rm_force(target)
            if proc.ok:
                removed.append(target)
                return
            err = (proc.stderr or proc.stdout or "").strip()
            if err and "No such container" not in err:
                errors.append(f"{target}: {err}")

        try:
            _rm(dedicated)
        except Exception as exc:
            errors.append(f"{dedicated}: {exc}")

        # Leftover containers tagged for this project (name mismatch / duplicates).
        try:
            for cid in cli.ps_ids(f"label=peon.project_id={pid}"):
                if cid in removed or cid == dedicated:
                    continue
                try:
                    _rm(cid)
                except Exception as exc:
                    errors.append(f"{cid}: {exc}")
        except Exception as exc:
            errors.append(str(exc))

        SandboxSession.reset()
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

    # --- volume helpers (policy) ---

    def _skills_tools(
        self, skills_dir: Path | None, tools_dir: Path | None
    ) -> tuple[Path, Path]:
        skills = Path(skills_dir or getattr(settings, "SKILLS_DIR", "skills")).resolve()
        tools = Path(
            tools_dir or getattr(settings, "TOOLS_CATALOG_DIR", "tools/catalog")
        ).resolve()
        tools_root = tools.parent if tools.name == "catalog" else tools
        return skills, tools_root

    def _socket_volume_args(self) -> list[str]:
        sock = str(getattr(settings, "STREAM_SOCKET_PATH", "") or "")
        sockets_vol = str(getattr(settings, "SANDBOX_SOCKETS_VOLUME", "") or "").strip()
        cli = self._cli()
        if sockets_vol:
            return ["-v", f"{sockets_vol}:/tmp/peon"]
        if sock:
            parent = Path(sock).parent
            return ["-v", f"{cli.host_bind_path(parent)}:{parent}"]
        return []

    def _volume_args_per_project(
        self,
        ws: Path,
        skills_dir: Path | None,
        tools_dir: Path | None,
    ) -> tuple[list[str], str]:
        """Mount only this project's workspace — never the shared data root."""
        skills, tools_root = self._skills_tools(skills_dir, tools_dir)
        cli = self._cli()
        container_ws = "/workspace"
        volume_args = [
            "-v",
            f"{cli.host_bind_path(ws)}:/workspace",
            "-v",
            f"{cli.host_bind_path(skills)}:/skills:ro",
            "-v",
            f"{cli.host_bind_path(tools_root)}:/tools:ro",
            *self._socket_volume_args(),
        ]
        return volume_args, container_ws

    def _volume_args_shared(
        self,
        ws: Path,
        skills_dir: Path | None,
        tools_dir: Path | None,
    ) -> tuple[list[str], str]:
        """Mount all project workspaces; cwd is this job's folder under /workspace."""
        skills, tools_root = self._skills_tools(skills_dir, tools_dir)
        root = Path(
            getattr(settings, "PROJECT_WORKSPACES_DIR", ws.parent)
        ).resolve()
        try:
            rel = ws.resolve().relative_to(root).as_posix()
        except ValueError:
            rel = ws.name
        container_ws = f"/workspace/{rel}" if rel else "/workspace"
        cli = self._cli()
        volume_args = [
            "-v",
            f"{cli.host_bind_path(root)}:/workspace",
            "-v",
            f"{cli.host_bind_path(skills)}:/skills:ro",
            "-v",
            f"{cli.host_bind_path(tools_root)}:/tools:ro",
            *self._socket_volume_args(),
        ]
        return volume_args, container_ws
