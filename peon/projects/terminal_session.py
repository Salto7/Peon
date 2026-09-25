"""Interactive PTY sessions into a project's Docker sandbox.

Operator HITL only — not used by agents. Keys (Ctrl+C, Ctrl+R, …) are handled
by the browser xterm when the panel is focused; this module just shuttles bytes.
"""

from __future__ import annotations

import fcntl
import logging
import os
import select
import struct
import subprocess
import termios
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from django.conf import settings

from peon.projects.sandbox import ProjectSandbox
from peon.projects.workspaces import project_workspace_dir

logger = logging.getLogger(__name__)

_MAX_SESSIONS_PER_PROJECT = 4
_IDLE_SECONDS = 30 * 60
_READ_CHUNK = 16_384
_MAX_INPUT = 64 * 1024


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    rows = max(1, min(int(rows or 24), 500))
    cols = max(1, min(int(cols or 80), 500))
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except OSError:
        pass


@dataclass
class TerminalSession:
    id: str
    project_id: str
    container: str
    master_fd: int
    proc: subprocess.Popen
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    cols: int = 80
    rows: int = 24
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def touch(self) -> None:
        self.last_active = time.time()

    def write(self, data: bytes) -> None:
        if not data:
            return
        if len(data) > _MAX_INPUT:
            data = data[:_MAX_INPUT]
        with self._lock:
            self.touch()
            os.write(self.master_fd, data)

    def resize(self, cols: int, rows: int) -> None:
        with self._lock:
            self.cols = max(1, min(int(cols or 80), 500))
            self.rows = max(1, min(int(rows or 24), 500))
            self.touch()
            _set_winsize(self.master_fd, self.rows, self.cols)

    def read_ready(self, timeout: float = 0.25) -> bytes:
        with self._lock:
            fd = self.master_fd
        try:
            ready, _, _ = select.select([fd], [], [], timeout)
        except (ValueError, OSError):
            return b""
        if not ready:
            return b""
        try:
            chunk = os.read(fd, _READ_CHUNK)
        except OSError:
            return b""
        if chunk:
            self.touch()
        return chunk or b""

    def alive(self) -> bool:
        return self.proc.poll() is None

    def close(self) -> None:
        with self._lock:
            try:
                if self.proc.poll() is None:
                    self.proc.terminate()
                    try:
                        self.proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.proc.kill()
            except Exception:
                pass
            try:
                os.close(self.master_fd)
            except OSError:
                pass


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

        container = self._ensure_container(pid)
        master, slave = os.openpty()
        _set_winsize(slave, rows, cols)
        # Keep slave open until Popen claims it.
        docker = ProjectSandbox.shared()._cli().bin() or "docker"
        workdir = "/workspace"
        try:
            proc = subprocess.Popen(
                [
                    docker,
                    "exec",
                    "-i",
                    "-t",
                    "-w",
                    workdir,
                    "-e",
                    "TERM=xterm-256color",
                    "-e",
                    "LANG=C.UTF-8",
                    container,
                    "/bin/bash",
                    "-l",
                ],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                start_new_session=True,
                close_fds=True,
            )
        except Exception:
            os.close(master)
            os.close(slave)
            raise
        finally:
            try:
                os.close(slave)
            except OSError:
                pass

        sid = uuid.uuid4().hex
        session = TerminalSession(
            id=sid,
            project_id=pid,
            container=container,
            master_fd=master,
            proc=proc,
            cols=int(cols or 80),
            rows=int(rows or 24),
        )
        with self._lock:
            self._sessions[sid] = session
            self._by_project.setdefault(pid, set()).add(sid)
        logger.info("terminal session %s → %s", sid[:8], container)
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

    def _ensure_container(self, project_id: str) -> str:
        sb = ProjectSandbox.shared()
        cli = sb._cli()
        if not cli.available():
            raise RuntimeError("Docker CLI unavailable — cannot open sandbox terminal")
        name = ProjectSandbox.container_name(project_id)
        exists, running = cli.inspect_running(name)
        if exists and running:
            return name
        ws = project_workspace_dir(project_id, create=True)
        skills = Path(getattr(settings, "SKILLS_DIR", "") or "")
        tools = Path(getattr(settings, "TOOLS_CATALOG_DIR", "") or "")
        info = ProjectSandbox.provision(
            project_id,
            workspace=ws,
            skills_dir=skills if skills.is_dir() else None,
            tools_dir=tools if tools.is_dir() else None,
        )
        return info.name


_REGISTRY: TerminalRegistry | None = None
_REGISTRY_LOCK = threading.Lock()


def get_registry() -> TerminalRegistry:
    global _REGISTRY
    with _REGISTRY_LOCK:
        if _REGISTRY is None:
            _REGISTRY = TerminalRegistry()
        return _REGISTRY
