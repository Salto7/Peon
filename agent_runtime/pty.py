"""PTY spawn used only by runtime backends."""

from __future__ import annotations

import fcntl
import os
import select
import struct
import subprocess
import termios
import threading
import time

from agent_runtime.api import TerminalHandle

_READ_CHUNK = 16_384
_MAX_INPUT = 64 * 1024


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    rows = max(1, min(int(rows or 24), 500))
    cols = max(1, min(int(cols or 80), 500))
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except OSError:
        pass


class PtyTerminal(TerminalHandle):
    def __init__(self, name: str, *, argv: list[str], cols: int, rows: int) -> None:
        super().__init__(name)
        self.cols = max(1, min(int(cols or 80), 500))
        self.rows = max(1, min(int(rows or 24), 500))
        self._lock = threading.Lock()
        self.last_active = time.time()
        master, slave = os.openpty()
        _set_winsize(slave, self.rows, self.cols)
        try:
            self._proc = subprocess.Popen(
                argv,
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
        self._master = master

    def touch(self) -> None:
        self.last_active = time.time()

    def write(self, data: bytes) -> None:
        if not data:
            return
        if len(data) > _MAX_INPUT:
            data = data[:_MAX_INPUT]
        with self._lock:
            self.touch()
            os.write(self._master, data)

    def resize(self, cols: int, rows: int) -> None:
        with self._lock:
            self.cols = max(1, min(int(cols or 80), 500))
            self.rows = max(1, min(int(rows or 24), 500))
            self.touch()
            _set_winsize(self._master, self.rows, self.cols)

    def read(self, timeout: float = 0.25) -> bytes:
        with self._lock:
            fd = self._master
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
        return self._proc.poll() is None

    def close(self) -> None:
        with self._lock:
            try:
                if self._proc.poll() is None:
                    self._proc.terminate()
                    try:
                        self._proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self._proc.kill()
            except Exception:
                pass
            try:
                os.close(self._master)
            except OSError:
                pass
