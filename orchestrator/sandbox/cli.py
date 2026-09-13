"""Docker CLI facade — inspect / lifecycle helpers shared by peon policies.

Policy (project mounts, learn-lab singleton, who may delete) stays in peon.
This module only talks to the docker binary; it never imports Django or peon.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from orchestrator.sandbox.backend import ExecResult, run_process
from orchestrator.utils.service import SharedService

logger = logging.getLogger(__name__)


class DockerCli(SharedService):
    """Thin wrapper around the docker CLI for sandbox / lab lifecycle."""

    def __init__(self) -> None:
        self._self_mounts_cache: list[dict[str, Any]] | None = None

    def bin(self) -> str | None:
        return shutil.which("docker")

    def require_bin(self) -> str:
        path = self.bin()
        if not path:
            raise RuntimeError("docker CLI missing")
        return path

    def available(self) -> bool:
        return bool(self.bin())

    def run(self, args: list[str], *, timeout: float = 120) -> ExecResult:
        """Run ``docker <args>``; raises if the CLI binary is missing."""
        return run_process([self.require_bin(), *args], timeout=timeout)

    def daemon_ok(self) -> tuple[bool, str]:
        """Return ``(reachable, error_or_empty)`` via ``docker info``."""
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
        """Return ``(exists, running)`` for a container name or id."""
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

    def rename(self, src: str, dest: str, *, timeout: float = 30) -> ExecResult:
        return self.run(["rename", src, dest], timeout=timeout)

    def ps_ids(self, *filters: str, all_containers: bool = True) -> list[str]:
        """Container ids matching ``--filter`` expressions (``label=…``, etc.)."""
        args = ["ps", "-aq" if all_containers else "-q"]
        for f in filters:
            args.extend(["--filter", f])
        listed = self.run(args, timeout=30)
        if not listed.ok:
            return []
        return [
            line.strip()
            for line in (listed.stdout or "").splitlines()
            if line.strip()
        ]

    def run_detached(
        self,
        *,
        name: str,
        image: str,
        workdir: str = "/tmp",
        labels: list[str] | None = None,
        volume_args: list[str] | None = None,
        command: list[str] | None = None,
        timeout: float = 180,
    ) -> ExecResult:
        """``docker run -d --name …`` with optional labels/volumes.

        ``labels`` are full ``key=value`` strings (passed as ``--label``).
        ``volume_args`` are already-formed argv fragments (e.g. ``["-v", "a:b"]``).
        """
        args: list[str] = ["run", "-d", "--name", name]
        for label in labels or []:
            args.extend(["--label", label])
        if volume_args:
            args.extend(volume_args)
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
        command: list[str] | None = None,
        pull_image: bool = False,
    ) -> str:
        """Start existing container or create once. Returns action string.

        Actions: ``reused`` | ``started`` | ``created``.
        On create name-conflict races, reconnects and returns ``reused``.
        """
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
        """Map a path inside this process to a path the Docker daemon can bind."""
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

    def self_mounts(self) -> list[dict[str, Any]]:
        """Mounts of the current container when running under Docker; else ``[]``."""
        if self._self_mounts_cache is not None:
            return self._self_mounts_cache
        if not Path("/.dockerenv").exists() or not self.bin():
            self._self_mounts_cache = []
            return self._self_mounts_cache
        cid = Path("/etc/hostname").read_text(encoding="utf-8").strip()
        if not cid:
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

    @staticmethod
    def sanitize_name_fragment(value: str) -> str:
        return "".join(
            ch if ch.isalnum() or ch in "._-" else "-" for ch in str(value).strip()
        ).strip("-")
