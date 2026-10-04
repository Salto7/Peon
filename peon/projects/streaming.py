"""Persist stream lines from Unix socket or worker emit."""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from django.conf import settings

from orchestrator.rpc import RpcServer
from peon.projects.models import Job, JobStatus, Project, StreamMessage, StreamMessageType

logger = logging.getLogger(__name__)

_VALID_TYPES = frozenset(StreamMessageType.values)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_LIVE = frozenset({JobStatus.RUNNING, JobStatus.PENDING, JobStatus.PAUSED})


def anchor_job(project: Project, *, prefer_live: bool = True) -> Job | None:
    """Pick a job to hang stream/audit lines on."""
    roots = project.jobs.filter(parent__isnull=True)
    if prefer_live:
        live = roots.filter(status__in=_LIVE).order_by("-updated_at").first()
        if live is not None:
            return live
    return (
        roots.order_by("-updated_at").first()
        or project.jobs.order_by("-updated_at").first()
    )


def has_live_agents(project: Project) -> bool:
    return project.jobs.filter(status__in=_LIVE).exists()


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


def emit_job_stream(
    job: Job | str,
    message_type: str,
    content: str,
    metadata: dict | None = None,
    *,
    swallow_errors: bool = False,
) -> StreamMessage | None:
    """Persist a stream line for a Job (or job id). Optional soft-fail for workers."""
    jid = str(getattr(job, "id", job) or "").strip()
    if not jid:
        return None
    try:
        return record_stream_message(jid, message_type, content, metadata)
    except Exception:
        if swallow_errors:
            logger.debug("emit_job_stream soft-failed for %s", jid, exc_info=True)
            return None
        raise


def stream_meta(
    project: Project | str | None,
    *,
    role: str = "assistant",
    tag: str = "",
    event: str = "",
    payload: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Standard console/feed metadata blob for ``record_stream_message``.

    Prefer ``event`` + ``payload`` for machine-readable structure; keep
    free-text in ``content``. Consumers should use
    ``orchestrator.utils.stream_events`` getters.
    """
    meta: dict[str, Any] = {"role": role}
    if tag:
        meta["tag"] = tag
    if event:
        meta["event"] = str(event).strip()
    if payload is not None:
        meta["payload"] = dict(payload)
    pid = str(getattr(project, "id", project) or "").strip()
    if pid:
        meta["project_id"] = pid
    meta.update(extra)
    return meta


def audit_project_stream(
    project: Project | str | None,
    text: str,
    *,
    tag: str = "",
    event: str = "",
    role: str = "assistant",
    message_type: str = "log",
    job: Job | None = None,
    prefer_live: bool = True,
    swallow_errors: bool = True,
) -> Job | None:
    """Anchor a project job (if needed) and emit one audited stream line."""
    if not (text or "").strip():
        return job
    if job is None:
        job = anchor_job(project, prefer_live=prefer_live) if project is not None else None
    if job is None:
        return None
    emit_job_stream(
        job,
        message_type,
        text,
        stream_meta(project, role=role, tag=tag, event=event),
        swallow_errors=swallow_errors,
    )
    return job


def project_message_dicts(
    project: Project,
    *,
    after_id: int = 0,
    limit: int = 200,
    newest_first: bool = False,
) -> list[dict[str, Any]]:
    """Serialize project stream lines for bootstrap or poll."""
    qs = (
        StreamMessage.objects.filter(job__project=project)
        .select_related("job")
    )
    if after_id > 0:
        qs = qs.filter(id__gt=after_id).order_by("id")
    elif newest_first:
        qs = qs.order_by("-id")
    else:
        qs = qs.order_by("id")
    rows = list(qs[: max(1, min(limit, 500))])
    if newest_first:
        rows = list(reversed(rows))
    return [message_to_dict(attach_job_context(m, project)) for m in rows]


def attach_job_context(
    msg: StreamMessage,
    project: Project | None = None,
) -> StreamMessage:
    """Ensure payload title/status/project attrs for ``message_to_dict``."""
    job = getattr(msg, "job", None)
    if job is not None:
        msg._payload_title = job.title  # type: ignore[attr-defined]
        msg._payload_status = job.status  # type: ignore[attr-defined]
    if project is not None:
        msg._payload_project = str(project.id)  # type: ignore[attr-defined]
    elif job is not None and getattr(job, "project_id", None):
        msg._payload_project = str(job.project_id)  # type: ignore[attr-defined]
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


def start_stream_server(
    path: str | None = None,
    *,
    on_message: Callable[[dict], None] | None = None,
) -> StreamSocketServer:
    """Build and start the Unix NDJSON stream listener (settings path by default)."""

    sock = path or getattr(settings, "STREAM_SOCKET_PATH", "/tmp/peon/stream.sock")
    server = StreamSocketServer(sock, on_message or handle_incoming_message)
    server.start()
    return server


def run_until_signal(*, on_stop: Callable[[], None] | None = None) -> None:
    """Block until SIGINT/SIGTERM, then optionally call ``on_stop``."""

    stop = False

    def _stop(*_args: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:
        while not stop:
            time.sleep(0.5)
    finally:
        if on_stop is not None:
            on_stop()


def start_peon_rpc_server():
    """Listen on ``RPC_SOCKET_PATH`` with the shared ``RPC_TOKEN``."""

    path = str(getattr(settings, "RPC_SOCKET_PATH", "") or "").strip()
    if not path:
        raise RuntimeError("RPC_SOCKET_PATH is not configured")
    token = str(getattr(settings, "RPC_TOKEN", "") or "").strip() or None
    server = RpcServer.start(path, token=token)
    logger.info("RPC Unix socket listening on %s", path)
    return server

