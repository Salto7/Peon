"""Interactive PTY sessions into a project's agent runtime.

Operator HITL only — not used by agents. Keys (Ctrl+C, Ctrl+R, …) are handled
by the browser xterm when the panel is focused; this module just shuttles bytes.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from django.conf import settings

from agent_runtime.api import TerminalHandle
from peon.projects.sandbox import ProjectSandbox
from peon.projects.workspaces import project_workspace_dir

logger = logging.getLogger(__name__)

_MAX_SESSIONS_PER_PROJECT = 4
_IDLE_SECONDS = 30 * 60
_MAX_INPUT = 64 * 1024


@dataclass
class TerminalSession:
    id: str
    project_id: str
    container: str
    handle: TerminalHandle
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    cols: int = 80
    rows: int = 24

    def touch(self) -> None:
        self.last_active = time.time()
        touch = getattr(self.handle, "touch", None)
        if touch:
            touch()

    def write(self, data: bytes) -> None:
        if not data:
            return
        if len(data) > _MAX_INPUT:
            data = data[:_MAX_INPUT]
        self.touch()
        self.handle.write(data)

    def resize(self, cols: int, rows: int) -> None:
        self.cols = max(1, min(int(cols or 80), 500))
        self.rows = max(1, min(int(rows or 24), 500))
        self.touch()
        self.handle.resize(self.cols, self.rows)

    def read_ready(self, timeout: float = 0.25) -> bytes:
        chunk = self.handle.read(timeout)
        if chunk:
            self.touch()
        return chunk or b""

    def alive(self) -> bool:
        return self.handle.alive()

    def close(self) -> None:
        self.handle.close()


class TerminalRegistry:
    """Process-local session map (web process; requires docker.sock).

    ``open`` reuses one live PTY per project (refresh reconnects). Explicit
    Disconnect closes it; a later open starts a fresh shell. tmux attach is
    planned later — not used yet.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, TerminalSession] = {}
        self._by_project: dict[str, set[str]] = {}
        self._lock = threading.Lock()

    def open(
        self,
        project_id: str,
        *,
        cols: int = 80,
        rows: int = 24,
    ) -> tuple[TerminalSession, bool]:
        """Return ``(session, reused)``. Reuses a live session when present."""
        pid = str(project_id).strip()
        if not pid:
            raise ValueError("project_id required")

        self.reap()
        keep, extras = self._pick_live(pid)
        for sid, proj in extras:
            self.close(sid, proj)

        if keep is not None and keep.alive():
            keep.resize(cols, rows)
            keep.touch()
            logger.info("terminal session %s reused → %s", keep.id[:8], keep.container)
            return keep, True

        with self._lock:
            existing = self._by_project.get(pid) or set()
            if len(existing) >= _MAX_SESSIONS_PER_PROJECT:
                raise RuntimeError(
                    f"At most {_MAX_SESSIONS_PER_PROJECT} terminals per project"
                )

        info = self._ensure_container(pid)
        runtime = ProjectSandbox.shared().runtime_for(pid)
        roles_raw = str(getattr(settings, "ROLES_DIR", "") or "").strip()
        helpers_raw = str(getattr(settings, "HELPERS_DIR", "") or "").strip()
        tools_raw = str(getattr(settings, "TOOLS_CATALOG_DIR", "") or "").strip()
        roles = Path(roles_raw) if roles_raw else None
        helpers = Path(helpers_raw) if helpers_raw else None
        tools = Path(tools_raw) if tools_raw else None
        handle = runtime.open_terminal(
            ProjectSandbox.shared()._project_spec(
                pid,
                runtime_id=runtime.id,
                workspace=project_workspace_dir(pid, create=True),
                roles_dir=roles if roles and roles.is_dir() else None,
                helpers_dir=helpers if helpers and helpers.is_dir() else None,
                tools_dir=tools if tools and tools.is_dir() else None,
            ),
            cols=int(cols or 80),
            rows=int(rows or 24),
        )
        sid = uuid.uuid4().hex
        session = TerminalSession(
            id=sid,
            project_id=pid,
            container=info.name,
            handle=handle,
            cols=int(cols or 80),
            rows=int(rows or 24),
        )
        with self._lock:
            self._sessions[sid] = session
            self._by_project.setdefault(pid, set()).add(sid)
        logger.info("terminal session %s → %s", sid[:8], info.name)
        return session, False

    def _pick_live(
        self, project_id: str
    ) -> tuple[TerminalSession | None, list[tuple[str, str]]]:
        """Newest live session for project + ids of extras to close."""
        with self._lock:
            ids = list(self._by_project.get(project_id) or ())
            live: list[TerminalSession] = []
            for sid in ids:
                sess = self._sessions.get(sid)
                if sess is not None and sess.alive():
                    live.append(sess)
            if not live:
                return None, []
            live.sort(key=lambda s: s.last_active, reverse=True)
            keep = live[0]
            extras = [(s.id, s.project_id) for s in live[1:]]
            return keep, extras

    def get(self, session_id: str, project_id: str) -> TerminalSession | None:
        sid = str(session_id or "").strip()
        pid = str(project_id or "").strip()
        with self._lock:
            sess = self._sessions.get(sid)
            if sess is None or sess.project_id != pid:
                return None
            return sess

    def close(self, session_id: str, project_id: str) -> bool:
        sess = self.get(session_id, project_id)
        if sess is None:
            return False
        sess.close()
        with self._lock:
            self._sessions.pop(sess.id, None)
            ids = self._by_project.get(sess.project_id)
            if ids is not None:
                ids.discard(sess.id)
                if not ids:
                    self._by_project.pop(sess.project_id, None)
        return True

    def reap(self) -> None:
        now = time.time()
        dead: list[tuple[str, str]] = []
        with self._lock:
            for sid, sess in list(self._sessions.items()):
                if (not sess.alive()) or (now - sess.last_active > _IDLE_SECONDS):
                    dead.append((sid, sess.project_id))
        for sid, pid in dead:
            self.close(sid, pid)

    def iter_output(self, session: TerminalSession) -> Iterator[bytes]:
        """Yield PTY bytes until the session dies or is closed."""
        while True:
            if self.get(session.id, session.project_id) is None:
                break
            if not session.alive():
                # Drain remaining.
                while True:
                    chunk = session.read_ready(0.05)
                    if not chunk:
                        break
                    yield chunk
                break
            chunk = session.read_ready(0.35)
            if chunk:
                yield chunk

    def _ensure_container(self, project_id: str):
        ws = project_workspace_dir(project_id, create=True)
        roles_raw = str(getattr(settings, "ROLES_DIR", "") or "").strip()
        helpers_raw = str(getattr(settings, "HELPERS_DIR", "") or "").strip()
        tools_raw = str(getattr(settings, "TOOLS_CATALOG_DIR", "") or "").strip()
        roles = Path(roles_raw) if roles_raw else None
        helpers = Path(helpers_raw) if helpers_raw else None
        tools = Path(tools_raw) if tools_raw else None
        return ProjectSandbox.provision(
            project_id,
            workspace=ws,
            roles_dir=roles if roles and roles.is_dir() else None,
            helpers_dir=helpers if helpers and helpers.is_dir() else None,
            tools_dir=tools if tools and tools.is_dir() else None,
        )


_REGISTRY: TerminalRegistry | None = None
_REGISTRY_LOCK = threading.Lock()


def get_registry() -> TerminalRegistry:
    global _REGISTRY
    with _REGISTRY_LOCK:
        if _REGISTRY is None:
            _REGISTRY = TerminalRegistry()
        return _REGISTRY
