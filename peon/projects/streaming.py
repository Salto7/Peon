"""Persist stream lines from Unix socket or worker emit."""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from peon.projects.models import Job, StreamMessage, StreamMessageType

logger = logging.getLogger(__name__)

_VALID_TYPES = frozenset(StreamMessageType.values)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def scrub_stream_noise(content: object) -> str:
    text = str(content or "")
    text = _ANSI_RE.sub("", text)
    text = text.replace("\x00", "")
    # Collapse runaway blank lines from tool/TTY noise.
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:200_000]


def _job_id(job_id: str) -> str:
    if not job_id:
        raise ValueError("job_id is required for stream messages")
    try:
        return str(uuid.UUID(str(job_id)))
    except ValueError as exc:
        raise ValueError(f"Invalid job_id UUID: {job_id!r}") from exc


def record_stream_message(
    job_id: str,
    message_type: str,
    content: str,
    metadata: dict | None = None,
) -> StreamMessage | None:
    """Write a StreamMessage for a Job. Returns None if job missing."""
    jid = _job_id(job_id)
    row = Job.objects.filter(id=jid).values_list("status", "parent_id", "title", "project_id").first()
    if row is None:
        logger.warning("Stream message for unknown job %s", jid)
        return None
    status, parent_id, title, project_id = row
    project_uuid = str(project_id) if project_id else ""

    meta: dict[str, Any] = dict(metadata or {})
    meta.setdefault("job_id", jid)
    if project_uuid:
        meta.setdefault("project_id", project_uuid)

    mtype = message_type if message_type in _VALID_TYPES else StreamMessageType.LOG
    cleaned = scrub_stream_noise(content)
    if not cleaned:
        return None
    msg = StreamMessage.objects.create(
        job_id=jid,
        message_type=mtype,
        content=cleaned,
        metadata=meta,
    )

    if parent_id and not meta.get("mirrored"):
        try:
            record_stream_message(
                str(parent_id),
                mtype,
                f"[subagent:{(title or '')[:40]}] {content or ''}",
                {
                    **meta,
                    "mirrored": True,
                    "from_subagent": jid,
                    "from_subagent_title": title,
                },
            )
        except Exception:
            logger.debug("subagent mirror failed for %s", jid, exc_info=True)

    # Attach runtime status for poll clients (not stored on the row).
    msg._payload_status = status  # type: ignore[attr-defined]
    msg._payload_title = title or jid  # type: ignore[attr-defined]
    msg._payload_project = project_uuid  # type: ignore[attr-defined]
    return msg


def message_to_dict(msg: StreamMessage) -> dict[str, Any]:
    meta = msg.metadata or {}
    return {
        "id": msg.id,
        "message_type": msg.message_type,
        "content": msg.content,
        "metadata": meta,
        "created_at": msg.created_at.isoformat() if msg.created_at else "",
        "job_id": str(msg.job_id),
        "job_title": getattr(msg, "_payload_title", None)
        or meta.get("job_title")
        or str(msg.job_id),
        "job_status": getattr(msg, "_payload_status", None) or meta.get("job_status") or "",
        "project_id": getattr(msg, "_payload_project", None)
        or meta.get("project_id")
        or "",
    }


def handle_incoming_message(payload: dict) -> None:
    """Unix-socket ingest → persist."""
    job_id = payload.get("job_id") or payload.get("task_id")
    if not job_id:
        logger.warning("Stream message missing job_id/task_id: %s", payload)
        return
    meta = dict(payload.get("metadata") or {})
    project_id = str(payload.get("project_id") or meta.get("project_id") or "").strip()
    if project_id:
        meta.setdefault("project_id", project_id)
    meta.setdefault("job_id", str(job_id))
    try:
        record_stream_message(
            str(job_id),
            str(payload.get("message_type") or "log"),
            scrub_stream_noise(payload.get("content", "")),
            meta,
        )
    except Exception:
        logger.exception("handle_incoming_message failed for job_id=%s", job_id)


# --- Unix socket server ---

_RECV_SIZE = 65_536
_CLIENT_TIMEOUT = 30.0
_ACCEPT_TIMEOUT = 1.0
_LISTEN_BACKLOG = 128
_HANDLER_WORKERS = 8
_MAX_BUFFER = 4 * 1024 * 1024


class StreamSocketServer:
    """Accept AF_UNIX clients; each line is one JSON object passed to on_message."""

    def __init__(self, socket_path: str, on_message: Callable[[dict], None]):
        self.socket_path = socket_path
        self.on_message = on_message
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._pool: ThreadPoolExecutor | None = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        parent = os.path.dirname(self.socket_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

        self._pool = ThreadPoolExecutor(
            max_workers=_HANDLER_WORKERS, thread_name_prefix="stream-sock"
        )
        self._running = True
        self._thread = threading.Thread(
            target=self._serve, name="stream-sock-accept", daemon=True
        )
        self._thread.start()
        logger.info("Stream Unix socket listening on %s", self.socket_path)

    def stop(self) -> None:
        self._running = False
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass

    def _serve(self) -> None:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server = server
        try:
            server.bind(self.socket_path)
            os.chmod(self.socket_path, 0o666)
            server.listen(_LISTEN_BACKLOG)
            server.settimeout(_ACCEPT_TIMEOUT)
            while self._running:
                try:
                    conn, _ = server.accept()
                except TimeoutError:
                    continue
                except OSError:
                    if self._running:
                        logger.exception("Unix socket accept error")
                    break
                pool = self._pool
                if pool is None:
                    conn.close()
                    continue
                pool.submit(self._handle_client, conn)
        finally:
            try:
                server.close()
            except OSError:
                pass

    def _handle_client(self, conn: socket.socket) -> None:
        buf = b""
        try:
            conn.settimeout(_CLIENT_TIMEOUT)
            while self._running:
                chunk = conn.recv(_RECV_SIZE)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > _MAX_BUFFER:
                    logger.warning("Stream socket client exceeded buffer; closing")
                    break
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning("Invalid JSON on stream socket: %s", line[:100])
                        continue
                    if not isinstance(payload, dict):
                        continue
                    try:
                        self.on_message(payload)
                    except Exception:
                        logger.exception("Stream socket on_message failed")
        except (ConnectionResetError, TimeoutError, OSError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass
