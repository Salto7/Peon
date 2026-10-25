"""Interactive PTY sessions into a project's agent runtime.

Operator HITL only — not used by agents. Keys (Ctrl+C, Ctrl+R, …) are handled
by the browser xterm when the panel is focused; this module just shuttles bytes.

The registry is process-local: it only works while the web tier runs as a
single process (``runserver`` / one gunicorn worker with threads). A dev
autoreload drops every session; the browser reopens on ``404``.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field

from agent_runtime.api import TerminalHandle
from peon.projects.sandbox import ProjectSandbox

logger = logging.getLogger(__name__)

_MAX_SESSIONS_PER_PROJECT = 4
_IDLE_SECONDS = 30 * 60
_HEARTBEAT_SECONDS = 15.0


@dataclass
class TerminalSession:
    id: str
    project_id: str
    container: str
    handle: TerminalHandle
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    viewers: int = 0

    @property
    def cols(self) -> int:
        return int(getattr(self.handle, "cols", 80))

    @property
    def rows(self) -> int:
        return int(getattr(self.handle, "rows", 24))

    def touch(self) -> None:
        self.last_active = time.time()

    def write(self, data: bytes) -> None:
        if data:
            self.touch()
            self.handle.write(data)

    def resize(self, cols: int, rows: int) -> None:
        self.touch()
        self.handle.resize(cols, rows)

    def alive(self) -> bool:
        return self.handle.alive()

    def close(self) -> None:
        self.handle.close()


class TerminalRegistry:
    """Process-local session map. One live PTY per project; refresh reattaches."""

    def __init__(self) -> None:
        self._sessions: dict[str, TerminalSession] = {}
        self._lock = threading.Lock()
        self._open_locks: dict[str, threading.Lock] = {}

    def _project_lock(self, project_id: str) -> threading.Lock:
        with self._lock:
            return self._open_locks.setdefault(project_id, threading.Lock())

    def open(
        self, project_id: str, *, cols: int = 80, rows: int = 24
    ) -> tuple[TerminalSession, bool]:
        """Return ``(session, reused)``. Reuses the newest live session."""
        pid = str(project_id or "").strip()
        if not pid:
            raise ValueError("project_id required")
        self.reap()
        # Serialize opens per project so a double click does not spawn two shells.
        with self._project_lock(pid):
            live = self._live_for(pid)
            if live:
                keep, *extras = live
                for extra in extras:
                    self.close(extra.id, pid)
                keep.resize(cols, rows)
                logger.info("terminal session %s reused → %s", keep.id[:8], keep.container)
                return keep, True
            if len(self._for_project(pid)) >= _MAX_SESSIONS_PER_PROJECT:
                raise RuntimeError(
                    f"At most {_MAX_SESSIONS_PER_PROJECT} terminals per project"
                )
            info = ProjectSandbox.provision(pid, bind=False, warm_mcp=False)
            runtime, spec = ProjectSandbox.spec_for(pid)
            handle = runtime.open_terminal(spec, cols=int(cols or 80), rows=int(rows or 24))
            session = TerminalSession(
                id=uuid.uuid4().hex, project_id=pid, container=info.name, handle=handle
            )
            with self._lock:
                self._sessions[session.id] = session
            logger.info("terminal session %s → %s", session.id[:8], info.name)
            return session, False

    def _for_project(self, project_id: str) -> list[TerminalSession]:
        with self._lock:
            return [s for s in self._sessions.values() if s.project_id == project_id]

    def _live_for(self, project_id: str) -> list[TerminalSession]:
        live = [s for s in self._for_project(project_id) if s.alive()]
        live.sort(key=lambda s: s.last_active, reverse=True)
        return live

    def get(self, session_id: str, project_id: str) -> TerminalSession | None:
        with self._lock:
            sess = self._sessions.get(str(session_id or "").strip())
        if sess is None or sess.project_id != str(project_id or "").strip():
            return None
        return sess

    def close(self, session_id: str, project_id: str) -> bool:
        sess = self.get(session_id, project_id)
        if sess is None:
            return False
        with self._lock:
            self._sessions.pop(sess.id, None)
        sess.close()
        return True

    def close_project(self, project_id: str) -> int:
        """Close every PTY session for a project (e.g. before workspace delete)."""
        pid = str(project_id or "").strip()
        return sum(1 for s in self._for_project(pid) if self.close(s.id, pid))

    def reap(self) -> None:
        now = time.time()
        with self._lock:
            dead = [
                s
                for s in self._sessions.values()
                if not s.alive()
                or (s.viewers == 0 and now - s.last_active > _IDLE_SECONDS)
            ]
        for sess in dead:
            self.close(sess.id, sess.project_id)

    def iter_output(self, session: TerminalSession) -> Iterator[bytes | None]:
        """Yield PTY bytes (replaying scrollback first) until the session ends.

        Yields ``None`` as a heartbeat so the caller can write a keep-alive
        and notice a client that went away.
        """
        handle = session.handle
        cursor = handle.cursor()
        last_beat = time.monotonic()
        with self._lock:
            session.viewers += 1
        try:
            while self.get(session.id, session.project_id) is not None:
                data, cursor, eof = handle.read_since(cursor, 0.5)
                if data:
                    yield data
                    last_beat = time.monotonic()
                elif eof:
                    break
                elif time.monotonic() - last_beat >= _HEARTBEAT_SECONDS:
                    yield None
                    last_beat = time.monotonic()
        finally:
            with self._lock:
                session.viewers = max(0, session.viewers - 1)
                session.touch()


_REGISTRY = TerminalRegistry()


def get_registry() -> TerminalRegistry:
    return _REGISTRY
