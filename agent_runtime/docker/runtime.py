"""Docker runtime: provision, exec, destroy, terminal."""

from __future__ import annotations

import logging
from pathlib import Path

from agent_runtime.api import Runtime, RuntimeSession, RuntimeSpec, SessionInfo, TerminalHandle
from agent_runtime.docker.cli import DockerCli
from agent_runtime.docker.session import DockerSession
from agent_runtime.pty import PtyTerminal

logger = logging.getLogger(__name__)


def _name_for(project_id: str, *, prefix: str, shared: bool) -> str:
    prefix = (prefix or "peon-project").strip()
    if shared or not (project_id or "").strip():
        return f"{prefix}-shared"
    safe = DockerCli.sanitize_name_fragment(project_id)
    budget = max(8, 63 - len(prefix) - 1)
    return f"{prefix}-{safe[:budget]}"


class DockerRuntime(Runtime):
    id = "sandbox"

    def resource_name(self, project_id: str, *, prefix: str, shared: bool) -> str:
        return _name_for(project_id, prefix=prefix, shared=shared)

    def _cli(self) -> DockerCli:
        return DockerCli.shared()

    def _labels(self, spec: RuntimeSpec) -> list[str]:
        return [f"{key}={value}" for key, value in (spec.labels or {}).items() if key]

    def _volumes(self, spec: RuntimeSpec) -> list[str]:
        cli = self._cli()
        args: list[str] = []
        if spec.workspace_host:
            args.extend(
                ["-v", f"{cli.host_bind_path(Path(spec.workspace_host))}:/workspace"]
            )
        if spec.skills_host:
            args.extend(["-v", f"{cli.host_bind_path(Path(spec.skills_host))}:/skills:ro"])
        if spec.tools_host:
            tools = Path(spec.tools_host)
            tools_root = tools.parent if tools.name == "catalog" else tools
            args.extend(["-v", f"{cli.host_bind_path(tools_root)}:/tools:ro"])
        if spec.socket_volume:
            args.extend(["-v", f"{spec.socket_volume}:/tmp/peon"])
        elif spec.socket_dir_host:
            parent = Path(spec.socket_dir_host)
            args.extend(["-v", f"{cli.host_bind_path(parent)}:{parent}"])
        return args

    def provision(self, spec: RuntimeSpec) -> RuntimeSession:
        cli = self._cli()
        if not cli.available():
            raise RuntimeError(
                "docker CLI missing — skill execution requires a sandbox runtime "
                "(host execution is disabled)"
            )
        name = (spec.name or "").strip()
        if not name:
            raise RuntimeError("sandbox name is required")
        image = (spec.image or "").strip()
        if not image:
            raise RuntimeError("sandbox image is required")
        if spec.recreate:
            cli.rm_force(name)
        action = cli.ensure_running(
            name,
            image=image,
            workdir=spec.container_workdir or "/workspace",
            labels=self._labels(spec),
            volume_args=self._volumes(spec) or None,
            pull_image=spec.pull_image,
        )
        return self._session(spec, action=action)

    def attach(self, spec: RuntimeSpec) -> RuntimeSession:
        return self._session(spec, action="reused")

    def _session(self, spec: RuntimeSpec, *, action: str) -> DockerSession:
        mode = _mode(spec)
        info = SessionInfo(
            project_id=spec.project_id,
            name=spec.name,
            mode=mode,
            action=action,
            image=spec.image,
            workdir=spec.container_workdir or "/workspace",
        )
        session = DockerSession(info, docker_bin=self._cli().require_bin())
        try:
            session.load_base_commands()
        except Exception as exc:
            logger.info("base-command probe skipped for %s: %s", spec.name, exc)
        return session

    def destroy(self, spec: RuntimeSpec) -> dict:
        name = (spec.name or "").strip()
        if not name:
            return {"ok": False, "removed": False, "error": "name required"}
        if spec.role == "shared":
            return {"ok": True, "removed": False, "name": name, "reason": "shared"}
        cli = self._cli()
        if not cli.available():
            return {"ok": False, "removed": False, "name": name, "error": "docker unavailable"}
        proc = cli.rm_force(name)
        if proc.ok:
            return {"ok": True, "removed": True, "name": name}
        err = (proc.stderr or proc.stdout or "").strip()
        if "No such container" in err:
            return {"ok": True, "removed": False, "name": name, "reason": "not found"}
        return {"ok": False, "removed": False, "name": name, "error": err}

    def status(self, spec: RuntimeSpec) -> dict:
        name = spec.name
        image = spec.image
        cli = self._cli()
        ok, err = cli.daemon_ok()
        if not ok:
            return {
                "name": name,
                "image": image,
                "exists": False,
                "running": False,
                "docker": False,
                "error": (err or "")[:300],
            }
        exists, running = cli.inspect_running(name) if name else (False, False)
        return {
            "name": name,
            "image": image,
            "exists": exists,
            "running": running,
            "docker": True,
            "error": "",
        }

    def find(self, label: str) -> list[str]:
        cli = self._cli()
        if not cli.available() or not label:
            return []
        return cli.ps_ids(label)

    def open_terminal(self, spec: RuntimeSpec, *, cols: int, rows: int) -> TerminalHandle:
        cli = self._cli()
        if not cli.available():
            raise RuntimeError("Docker CLI unavailable — cannot open sandbox terminal")
        workdir = spec.container_workdir or "/workspace"
        argv = [
            cli.require_bin(),
            "exec",
            "-i",
            "-t",
            "-w",
            workdir,
            "-e",
            "TERM=xterm-256color",
            "-e",
            "LANG=C.UTF-8",
            spec.name,
            "/bin/bash",
            "-l",
        ]
        return PtyTerminal(spec.name, argv=argv, cols=cols, rows=rows)


def _mode(spec: RuntimeSpec) -> str:
    if spec.role == "learn-lab":
        return "learn-lab"
    if spec.role == "shared":
        return "shared"
    return "docker"
