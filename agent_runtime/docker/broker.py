"""Policy guard mediating container-spawn requests before they reach the daemon.

``DockerClient.run_detached`` (``agent_runtime/docker/engine.py``) is the single
seam both of Peon's container-spawn call sites go through
(``agent_runtime/docker/runtime.py``'s sandbox provisioning and
``peon/projects/llm_proxy.py``'s LiteLLM sidecar). This module is not a second
daemon or process — it's a guard function invoked inline at that seam, before
the ``docker.from_env()`` SDK call.

``run_detached`` never exposes a ``privileged`` flag to its callers, so that
escalation path is already structurally absent. The one real residual risk is
a bind mount that hands a spawned container the host's root filesystem or its
Docker socket (which is itself a straightforward path to daemon/host access,
equivalent to running ``--privileged``) — that's what this module blocks,
unconditionally. A secondary, opt-in image allowlist is available for
operators who want to pin exactly which images may be spawned; it is off by
default so it cannot break an existing per-project custom sandbox image.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_SENSITIVE_EXACT: frozenset[str] = frozenset({"/", "/var/run/docker.sock", "/run/docker.sock"})
_SENSITIVE_PREFIXES: frozenset[str] = frozenset(
    {"/etc", "/root", "/boot", "/var/run/docker.sock", "/run/docker.sock"}
)


class SandboxPolicyError(RuntimeError):
    """Raised when a container-spawn request violates the broker's policy."""


# On by default (unlike the image allowlist): nothing else in Peon stops N
# concurrent project sandboxes from jointly exhausting host memory, and the
# failure mode when that happens is the Docker daemon/host itself getting
# OOM-killed, not a clean per-job error. Set to 0 to disable (e.g. when host
# memory limits are already enforced elsewhere, such as a cgroup/k8s quota).
_DEFAULT_MIN_AVAILABLE_MB = 512


def available_memory_mb() -> int | None:
    """``MemAvailable`` from ``/proc/meminfo`` in MB, or ``None`` if unreadable.

    ``MemAvailable`` (not ``MemFree``) is what the kernel itself estimates is
    reclaimable for a new allocation without swapping — it already accounts
    for reclaimable cache/buffers the way a raw free-memory reading would not.
    """
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def assert_memory_available() -> None:
    """Deny a spawn when the host is low on memory.

    Fails open when ``/proc/meminfo`` can't be read (non-Linux, or some
    unusual container runtime) — this is a best-effort stability guard, not
    a security boundary, and refusing every spawn because the one read-only
    diagnostic file is unavailable would be the worse failure mode.
    """
    raw = os.environ.get("DOCKER_BROKER_MIN_AVAILABLE_MB", "")
    try:
        min_mb = int(raw) if raw.strip() else _DEFAULT_MIN_AVAILABLE_MB
    except ValueError:
        min_mb = _DEFAULT_MIN_AVAILABLE_MB
    if min_mb <= 0:
        return
    available = available_memory_mb()
    if available is None:
        return
    if available < min_mb:
        raise SandboxPolicyError(
            f"docker broker: only {available}MB of host memory available "
            f"(need >= {min_mb}MB) — refusing to spawn another sandbox "
            "container; retry once load drops"
        )


def _host_bind_denied(host_path: str) -> bool:
    """True when ``host_path`` is (or is under) a sensitive host location."""
    raw = (host_path or "").strip()
    if not raw:
        return False
    try:
        resolved = Path(raw).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        resolved = Path(raw)
    candidates = {str(resolved), raw.rstrip("/") or "/"}
    for candidate in candidates:
        norm = candidate.rstrip("/") or "/"
        if norm in _SENSITIVE_EXACT:
            return True
        if Path(norm).name == "docker.sock":
            return True
        for prefix in _SENSITIVE_PREFIXES:
            if norm == prefix or norm.startswith(prefix + "/"):
                return True
    return False


def assert_bind_mounts_allowed(volumes: list[str] | None) -> None:
    """Deny host-root / Docker-socket bind mounts in a ``host:container[:mode]`` list.

    Resolves ``.`` / ``..`` and blocks descendants (``/etc/shadow``, nested
    docker.sock paths). Exact-match alone let those through.
    """
    for spec in volumes or []:
        host_path = str(spec).split(":", 1)[0].strip()
        if _host_bind_denied(host_path):
            raise SandboxPolicyError(
                f"docker broker: refusing to bind-mount host path {host_path!r} into a "
                "sandbox container — this would grant host/daemon access"
            )


def assert_image_allowed(image: str) -> None:
    """Deny ``image`` when an operator-configured allowlist exists and excludes it.

    Off by default (empty allowlist = allow everything) — see module docstring.
    ``agent_runtime`` has no dependency on Django/Peon settings, so this reads
    a plain env var rather than ``PeonSettings`` (which lives one layer up).
    """
    raw = os.environ.get("DOCKER_BROKER_IMAGE_ALLOWLIST", "")
    allowlist = {item.strip() for item in raw.split(",") if item.strip()}
    if not allowlist:
        return
    if (image or "").strip() not in allowlist:
        raise SandboxPolicyError(
            f"docker broker: image {image!r} is not on DOCKER_BROKER_IMAGE_ALLOWLIST"
        )


def assert_spawn_allowed(*, image: str, volumes: list[str] | None) -> None:
    """Single entry point: run every broker check before a container is spawned."""
    assert_bind_mounts_allowed(volumes)
    assert_image_allowed(image)
    assert_memory_available()


__all__ = [
    "SandboxPolicyError",
    "assert_spawn_allowed",
    "available_memory_mb",
    "assert_memory_available",
]
