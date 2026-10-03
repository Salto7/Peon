"""Runtime-agnostic session interface.

Orchestrator and the control plane call this module only. Docker, OpenShell,
and any later backend stay in their own packages.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Callable

_BASE_CMDS_FILE = "/etc/peon/base-commands"

EnvLookup = Callable[[str, str], str]


@dataclass(frozen=True)
class ExecResult:
    code: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.code == 0


@dataclass
class SessionInfo:
    project_id: str
    name: str
    mode: str
    action: str = "existing"
    image: str = ""
    workdir: str = ""
    base_commands: frozenset[str] = field(default_factory=frozenset)


@dataclass
class RuntimeSpec:
    """Plain data the control plane hands a backend. No CLI flags."""

    project_id: str = ""
    name: str = ""
    image: str = ""
    role: str = "project"  # project | shared | learn-lab
    workspace_host: str = ""
    skills_host: str = ""
    tools_host: str = ""
    socket_dir_host: str = ""
    socket_volume: str = ""
    container_workdir: str = "/workspace"
    labels: dict[str, str] = field(default_factory=dict)
    recreate: bool = False
    pull_image: bool = False
    state_dir: str = ""


class RuntimeSession(ABC):
    """One bound environment. ``exec`` is the only way to run a command."""

    def __init__(self, info: SessionInfo) -> None:
        self.info = info
        self._base: frozenset[str] | None = None
        self._env_lookup: EnvLookup | None = None

    def set_env_lookup(self, lookup: EnvLookup | None) -> None:
        """Job env is supplied by the caller. Backends must not import it."""
        self._env_lookup = lookup

    def env_get(self, key: str, default: str = "") -> str:
        if self._env_lookup is None:
            return default
        try:
            return str(self._env_lookup(key, default) or default)
        except Exception:
            return default

    @property
    def bound(self) -> bool:
        return self.info.mode != "unbound"

    @abstractmethod
    def exec(
        self,
        cmd: list[str] | str,
        *,
        timeout: float | None = 300,
        shell: bool = False,
        cwd: str | None = None,
    ) -> ExecResult: ...

    @abstractmethod
    def which(self, binary: str) -> bool: ...

    def workdir(self) -> str:
        return (
            self.env_get("ORCHESTRATOR_SANDBOX_WORKDIR")
            or (self.info.workdir or "").strip()
            or "/workspace"
        )

    def describe(self) -> str:
        info = self.info
        return (
            f"name={info.name} mode={info.mode} "
            f"session_id={info.project_id or '-'}"
        )

    def base_commands(self) -> frozenset[str]:
        if self._base is not None:
            return self._base
        if self.info.base_commands:
            self._base = self.info.base_commands
            return self._base
        cache = BaseCommandCache.shared()
        key = cache.key(self.info.image, mode=self.info.mode)
        cached = cache.get(key)
        if cached is not None:
            self._base = cached
            return self._base
        discovered = self._read_base_commands()
        self._base = cache.put(key, discovered)
        return self._base

    def _read_base_commands(self) -> frozenset[str]:
        path = BaseCommandCache.marker_path()
        res = self.exec(
            f"if [ -f {path} ]; then cat {path}; fi",
            shell=True,
            timeout=30,
        )
        return BaseCommandCache.parse_marker(res.stdout)


class UnboundSession(RuntimeSession):
    """Fail closed. Nothing runs on the host."""

    def exec(
        self,
        cmd: list[str] | str,
        *,
        timeout: float | None = 300,
        shell: bool = False,
        cwd: str | None = None,
    ) -> ExecResult:
        del cmd, timeout, shell, cwd
        raise RuntimeError(
            "No agent runtime bound — provision a project runtime before executing"
        )

    def which(self, binary: str) -> bool:
        del binary
        return False

    def _read_base_commands(self) -> frozenset[str]:
        return frozenset()


class TerminalHandle(ABC):
    """Interactive shell owned by a runtime backend."""

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    def write(self, data: bytes) -> None: ...

    @abstractmethod
    def read(self, timeout: float = 0.25) -> bytes: ...

    @abstractmethod
    def resize(self, cols: int, rows: int) -> None: ...

    @abstractmethod
    def alive(self) -> bool: ...

    @abstractmethod
    def close(self) -> None: ...


class Runtime(ABC):
    """Lifecycle for one backend. Registered by id (``sandbox``, ``openshell``)."""

    id: str

    @abstractmethod
    def resource_name(self, project_id: str, *, prefix: str, shared: bool) -> str: ...

    @abstractmethod
    def provision(self, spec: RuntimeSpec) -> RuntimeSession: ...

    @abstractmethod
    def attach(self, spec: RuntimeSpec) -> RuntimeSession: ...

    @abstractmethod
    def destroy(self, spec: RuntimeSpec) -> dict: ...

    @abstractmethod
    def status(self, spec: RuntimeSpec) -> dict: ...

    @abstractmethod
    def open_terminal(self, spec: RuntimeSpec, *, cols: int, rows: int) -> TerminalHandle: ...

    def find(self, label: str) -> list[str]:
        del label
        return []


class BaseCommandCache:
    """One base-command set per image, shared inside the process."""

    _instance: BaseCommandCache | None = None

    def __init__(self) -> None:
        self._by_image: dict[str, frozenset[str]] = {}

    @classmethod
    def shared(cls) -> BaseCommandCache:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def marker_path() -> str:
        return _BASE_CMDS_FILE

    @staticmethod
    def key(image: str, *, mode: str = "") -> str:
        return (image or mode or "runtime").strip() or "runtime"

    def get(self, image_key: str) -> frozenset[str] | None:
        return self._by_image.get(self.key(image_key))

    def put(self, image_key: str, commands: frozenset[str]) -> frozenset[str]:
        key = self.key(image_key)
        frozen = frozenset(commands)
        self._by_image[key] = frozen
        return frozen

    @staticmethod
    def parse_marker(stdout: str) -> frozenset[str]:
        return frozenset(
            line.strip()
            for line in (stdout or "").splitlines()
            if line.strip() and not line.strip().startswith("#")
        )


_current: ContextVar[RuntimeSession | None] = ContextVar("agent_runtime_session", default=None)


class Session:
    """Context-local bound runtime. Tool threads inherit the context var."""

    def __init__(self) -> None:
        self._tls = threading.local()

    _shared: Session | None = None

    @classmethod
    def shared(cls) -> Session:
        if cls._shared is None:
            cls._shared = cls()
        return cls._shared

    @classmethod
    def current(cls) -> RuntimeSession:
        backend = _current.get()
        if backend is None:
            return UnboundSession(SessionInfo(project_id="", name="unbound", mode="unbound"))
        return backend

    @classmethod
    def bind(cls, backend: RuntimeSession | None) -> None:
        self = cls.shared()
        prev: Token | None = getattr(self._tls, "token", None)
        if prev is not None:
            try:
                _current.reset(prev)
            except LookupError:
                pass
            self._tls.token = None
        self._tls.token = _current.set(backend)

    @classmethod
    def reset(cls) -> None:
        self = cls.shared()
        token: Token | None = getattr(self._tls, "token", None)
        if token is not None:
            try:
                _current.reset(token)
            except LookupError:
                pass
            self._tls.token = None
            return
        if _current.get() is not None:
            _current.set(None)

    @classmethod
    def require_bound(cls) -> str | None:
        """Error string when nothing is bound. Host execution is never a fallback."""
        current = cls.current()
        if current.bound:
            return None
        return (
            "Error: no agent runtime bound (host execution disabled). "
            f"mode={current.info.mode!r}"
        )
