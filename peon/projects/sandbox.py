"""Project runtime lifecycle with one dedicated environment per Project."""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Any

from django.conf import settings

from agent_runtime.api import Runtime, RuntimeSpec, Session, SessionInfo
from agent_runtime.registry import RuntimeRegistry
from orchestrator.utils.job_env import JobEnv
from orchestrator.utils.paths import safe_workspace_key
from orchestrator.utils.service import SharedServiceBase
from peon.projects.models import Project, SandboxRuntime

logger = logging.getLogger(__name__)


def runtime_state_dir() -> Path:
    """Docker/OpenShell metadata in the reserved platform workspace."""
    root = Path(
        getattr(
            settings,
            "PLATFORM_WORKSPACE_DIR",
            Path.cwd() / "workspaces" / "platform",
        )
    ).resolve()
    return root / "runtime"


def bind_runtime_session(session: Any, *, default_workdir: str = "/workspace") -> None:
    """Set workdir env, JobEnv lookup, and ``Session.bind``."""

    work = (session.info.workdir or default_workdir).strip() or default_workdir
    os.environ["ORCHESTRATOR_SANDBOX_WORKDIR"] = work
    if JobEnv.current():
        JobEnv.bind({**JobEnv.current(), "ORCHESTRATOR_SANDBOX_WORKDIR": work})
    else:
        JobEnv.bind({"ORCHESTRATOR_SANDBOX_WORKDIR": work})
    session.set_env_lookup(JobEnv.get)
    Session.bind(session)


def runtime_id_for(project_id: str) -> str:
    pid = str(project_id or "").strip()
    if not pid:
        return SandboxRuntime.SANDBOX
    try:
        value = (
            Project.objects.filter(pk=pid)
            .values_list("sandbox_runtime", flat=True)
            .first()
        )
    except Exception:
        return SandboxRuntime.SANDBOX
    return SandboxRuntime.resolve(value)


class ProjectSandbox(SharedServiceBase):
    """Choose the project's runtime and bind a session. Does not run commands."""

    def per_project(self) -> bool:
        return True

    def prefix(self) -> str:
        return str(
            getattr(settings, "PROJECT_SANDBOX_PREFIX", "peon-project") or "peon-project"
        ).strip()

    def image_for(self, runtime_id: str) -> str:
        sandbox = str(
            getattr(settings, "SANDBOX_IMAGE", "peon-sandbox:local") or "peon-sandbox:local"
        ).strip()
        if runtime_id == SandboxRuntime.OPENSHELL:
            raw = str(getattr(settings, "OPENSHELL_IMAGE", "") or "").strip()
            return raw or sandbox
        return sandbox

    def runtime_for(self, project_id: str) -> Runtime:
        registry = RuntimeRegistry.shared()
        registry.register_builtin()
        return registry.get(runtime_id_for(project_id))

    @classmethod
    def container_name(cls, project_id: str) -> str:
        self = cls.shared()
        pid = str(project_id or "").strip()
        runtime = self.runtime_for(pid)
        return runtime.resource_name(
            pid,
            prefix=self.prefix(),
            shared=False,
        )

    @classmethod
    def provision(
        cls,
        project_id: str,
        *,
        bind: bool = True,
        warm_mcp: bool = True,
    ) -> SessionInfo:
        """Create or attach the project runtime.

        Job paths bind the session and warm the MCP supervisor. The operator
        terminal passes ``bind=False, warm_mcp=False``: it only needs the
        container up, and the MCP warm-up can take minutes on a cold image.
        """
        runtime, spec = cls.spec_for(str(project_id or "").strip())
        session = runtime.provision(spec)
        if bind:
            bind_runtime_session(session, default_workdir="/workspace")
        mode = (session.info.mode or "").strip().lower()
        if warm_mcp and mode and mode != "openshell":
            try:
                from orchestrator.mcp.provision import ensure_mcp_runtime

                warm = ensure_mcp_runtime(session)
                if warm:
                    logger.warning("mcp supervisor warm-up: %s", warm[:300])
            except Exception as exc:
                logger.warning("mcp supervisor warm-up skipped: %s", exc)
        return session.info

    @classmethod
    def remove(
        cls,
        project_id: str,
        *,
        runtime_id: str = "",
    ) -> dict[str, Any]:
        self = cls.shared()
        pid = str(project_id or "").strip()
        if not pid:
            return {"action": "skipped", "reason": "no project_id"}

        registry = RuntimeRegistry.shared()
        registry.register_builtin()
        selected = (
            SandboxRuntime.resolve(runtime_id)
            if (runtime_id or "").strip()
            else runtime_id_for(pid)
        )
        runtime = registry.get(selected)
        dedicated = runtime.resource_name(pid, prefix=self.prefix(), shared=False)
        removed: list[str] = []
        errors: list[str] = []

        def _rm(target: str, *, role: str) -> None:
            result = runtime.destroy(
                RuntimeSpec(
                    project_id=pid,
                    name=target,
                    role=role,
                    image=self.image_for(runtime.id),
                )
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
                if cid in removed or cid == dedicated:
                    continue
                try:
                    _rm(cid, role="project")
                except Exception as exc:
                    errors.append(f"{cid}: {exc}")
        except Exception as exc:
            errors.append(str(exc))

        policy = runtime_state_dir() / f"{dedicated}.policy.yaml"
        try:
            policy.unlink(missing_ok=True)
        except OSError as exc:
            errors.append(f"{policy}: {exc}")

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

    @classmethod
    def remove_all_backends(cls, project_id: str) -> dict[str, Any]:
        """Remove any runtime resource deterministically owned by a project."""
        self = cls.shared()
        pid = str(project_id or "").strip()
        registry = RuntimeRegistry.shared()
        registry.register_builtin()
        results: dict[str, dict[str, Any]] = {}
        for rid in registry.ids():
            runtime = registry.get(rid)
            name = runtime.resource_name(pid, prefix=self.prefix(), shared=False)
            spec = RuntimeSpec(
                project_id=pid,
                name=name,
                role="project",
                image=self.image_for(rid),
                state_dir=str(runtime_state_dir()),
            )
            try:
                status = runtime.status(spec)
            except Exception as exc:
                results[rid] = {"action": "skipped", "error": str(exc)}
                continue
            if not status.get("exists") and status.get("error"):
                results[rid] = {
                    "action": "skipped",
                    "error": str(status.get("error") or ""),
                }
                continue
            results[rid] = cls.remove(pid, runtime_id=rid)
        actions = {str(result.get("action") or "") for result in results.values()}
        if "error" in actions:
            action = "error"
        elif "removed" in actions:
            action = "removed"
        else:
            action = "missing"
        return {"action": action, "project_id": pid, "backends": results}

    @classmethod
    def spec_for(cls, project_id: str) -> tuple[Runtime, RuntimeSpec]:
        """Runtime and spec for the project's dedicated environment."""
        self = cls.shared()
        try:
            project_id = str(uuid.UUID(str(project_id)))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError("valid project_id is required for an isolated sandbox") from exc
        runtime = self.runtime_for(project_id)
        ws = Path(settings.PROJECT_WORKSPACES_DIR).resolve() / safe_workspace_key(project_id)
        ws.mkdir(parents=True, exist_ok=True)
        # Catalog packs are materialized from PlatformCatalog into the
        # workspace. Only the skill runtime helpers are mounted.
        helper_pack = Path(settings.SKILLS_DIR).resolve() / "helpers"
        extra_mounts = [(str(helper_pack), "/skills/helpers")] if helper_pack.is_dir() else []
        sock = str(getattr(settings, "STREAM_SOCKET_PATH", "") or "")
        volume = str(getattr(settings, "SANDBOX_SOCKETS_VOLUME", "") or "").strip()
        spec = RuntimeSpec(
            project_id=project_id,
            name=runtime.resource_name(project_id, prefix=self.prefix(), shared=False),
            image=self.image_for(runtime.id),
            role="project",
            workspace_host=str(ws),
            extra_ro_mounts=extra_mounts,
            tools_host=str(Path(settings.TOOLS_CATALOG_DIR).resolve()),
            socket_dir_host="" if volume else (str(Path(sock).parent) if sock else ""),
            socket_volume=volume,
            container_workdir=(
                "/sandbox" if runtime.id == SandboxRuntime.OPENSHELL else "/workspace"
            ),
            labels={"peon.role": "project-sandbox", "peon.project_id": project_id},
            state_dir=str(runtime_state_dir()),
        )
        return runtime, spec
