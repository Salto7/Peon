"""Docker CLI facade. Lifecycle only — command execution goes through DockerSession."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from agent_runtime.api import ExecResult
from agent_runtime.cli_base import CLIBase

logger = logging.getLogger(__name__)


class DockerCli(CLIBase):
    """Thin wrapper around the docker CLI."""

    binary_name = "docker"
    missing_error = "docker CLI missing"

    def __init__(self) -> None:
        self._self_mounts_cache: list[dict[str, Any]] | None = None

    def daemon_ok(self) -> tuple[bool, str]:
        if not self.bin():
            return False, "docker CLI missing"
        try:
            probe = self.run(["info", "-f", "{{.ServerVersion}}"], timeout=20)
        except RuntimeError as exc:
            return False, str(exc)
        if probe.ok:
            return True, ""
        return False, (probe.stderr or probe.stdout or "docker daemon unreachable").strip()

    def require_daemon(self) -> None:
        ok, err = self.daemon_ok()
        if not ok:
            raise RuntimeError(
                "Docker daemon unreachable — ensure the docker CLI can reach "
                f"the daemon (e.g. /var/run/docker.sock). Detail: {(err or '')[:400]}"
            )

    def inspect_running(self, name: str) -> tuple[bool, bool]:
        res = self.run(["inspect", "-f", "{{.State.Running}}", name], timeout=30)
        if not res.ok:
            return False, False
        return True, (res.stdout or "").strip().lower() == "true"

    def inspect_format(self, ref: str, fmt: str, *, timeout: float = 30) -> ExecResult:
        return self.run(["inspect", "-f", fmt, ref], timeout=timeout)

    def start(self, name: str, *, timeout: float = 60) -> ExecResult:
        return self.run(["start", name], timeout=timeout)

    def rm_force(self, ref: str, *, timeout: float = 60) -> ExecResult:
        return self.run(["rm", "-f", ref], timeout=timeout)

    def pull(self, image: str, *, timeout: float = 600) -> ExecResult:
        return self.run(["pull", image], timeout=timeout)

    def ps_ids(self, *filters: str, all_containers: bool = True) -> list[str]:
        args = ["ps", "-aq" if all_containers else "-q"]
        for item in filters:
            args.extend(["--filter", item])
        listed = self.run(args, timeout=30)
        if not listed.ok:
            return []
        return [line.strip() for line in (listed.stdout or "").splitlines() if line.strip()]

    def run_detached(
        self,
        *,
        name: str,
        image: str,
        workdir: str = "/tmp",
        labels: list[str] | None = None,
        volume_args: list[str] | None = None,
        network: str = "",
        env: dict[str, str] | None = None,
        extra_args: list[str] | None = None,
        command: list[str] | None = None,
        timeout: float = 180,
    ) -> ExecResult:
        args: list[str] = ["run", "-d", "--name", name]
        for label in labels or []:
            args.extend(["--label", label])
        if network:
            args.extend(["--network", network])
        for key, value in (env or {}).items():
            args.extend(["-e", f"{key}={value}"])
        if volume_args:
            args.extend(volume_args)
        if extra_args:
            args.extend(extra_args)
        args.extend(["-w", workdir, image])
        args.extend(command or ["sleep", "infinity"])
        return self.run(args, timeout=timeout)

    def ensure_running(
        self,
        name: str,
        *,
        image: str,
        workdir: str = "/tmp",
        labels: list[str] | None = None,
        volume_args: list[str] | None = None,
        network: str = "",
        env: dict[str, str] | None = None,
        extra_args: list[str] | None = None,
        command: list[str] | None = None,
        pull_image: bool = False,
    ) -> str:
        exists, running = self.inspect_running(name)
        if exists and running:
            return "reused"
        if exists and not running:
            start = self.start(name)
            if not start.ok:
                raise RuntimeError(
                    f"Failed to start container {name}: {(start.stderr or '').strip()}"
                )
            return "started"

        if pull_image:
            pull = self.pull(image)
            if not pull.ok:
                logger.warning(
                    "image pull soft-failed for %s: %s",
                    image,
                    (pull.stderr or pull.stdout or "").strip()[:200],
                )

        create = self.run_detached(
            name=name,
            image=image,
            workdir=workdir,
            labels=labels,
            volume_args=volume_args,
            network=network,
            env=env,
            extra_args=extra_args,
            command=command,
        )
        if create.ok:
            return "created"

        err = (create.stderr or create.stdout or "").strip()
        exists2, running2 = self.inspect_running(name)
        if exists2:
            if not running2:
                self.start(name)
            logger.info("container %s already existed after create race; reusing", name)
            return "reused"
        raise RuntimeError(f"Failed to create container {name}: {err}")

    def host_bind_path(self, path: Path) -> str:
        resolved = path.resolve()
        path_s = str(resolved)
        for mount in self.self_mounts():
            dest = str(mount.get("Destination") or "")
            src = str(mount.get("Source") or "")
            if not dest or not src:
                continue
            if path_s == dest or path_s.startswith(dest.rstrip("/") + "/"):
                rel = path_s[len(dest) :].lstrip("/")
                return str(Path(src) / rel) if rel else src
        return path_s

    def self_container_id(self) -> str:
        """Hostname of this process when running inside Docker; else empty."""
        if not Path("/.dockerenv").exists():
            return ""
        try:
            return Path("/etc/hostname").read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def self_mounts(self) -> list[dict[str, Any]]:
        if self._self_mounts_cache is not None:
            return self._self_mounts_cache
        cid = self.self_container_id()
        if not cid or not self.bin():
            self._self_mounts_cache = []
            return self._self_mounts_cache
        res = self.inspect_format(cid, "{{json .Mounts}}")
        if not res.ok or not (res.stdout or "").strip():
            self._self_mounts_cache = []
            return self._self_mounts_cache
        try:
            data = json.loads(res.stdout.strip())
        except json.JSONDecodeError:
            self._self_mounts_cache = []
            return self._self_mounts_cache
        self._self_mounts_cache = data if isinstance(data, list) else []
        return self._self_mounts_cache
