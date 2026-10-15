"""Server-sent events for live project consoles (replaces browser short-polling).

One long-lived stream per open console:

* ``messages`` — new ``StreamMessage`` rows, tailed by id. The SSE ``id:`` is
  the last message id, so a reconnecting ``EventSource`` resumes from
  ``Last-Event-ID`` without gaps or duplicates.
* ``invalidate`` — ``{"keys": [...]}`` when jobs / objectives / findings /
  prompts / assets / project status change, detected with a cheap aggregate
  fingerprint. Clients refetch the matching JSON endpoint only then.

Writers do not need to know about this module: the fingerprint catches every
change. ``notify_project`` (Redis pub/sub) is only a wake-up so new messages
reach the browser immediately instead of on the next tick; without Redis the
stream still works at tick granularity.

The generators are synchronous so they run under both ``runserver`` (WSGI)
and an ASGI server; each open stream holds one worker thread.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from typing import Any

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import close_old_connections
from django.db.models import Count, Max
from django.http import HttpRequest, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from peon.projects.http_helpers import sse_response

logger = logging.getLogger(__name__)

TICK_S = 1.0  # max latency for fingerprint-detected changes
HEARTBEAT_S = 15.0  # keeps proxies from closing idle streams
STREAM_LIFETIME_S = 15 * 60  # bounded; EventSource reconnects transparently
MESSAGE_BATCH = 200
_CHANNEL = "peon:project:{}"

_redis_client = None


def _redis():
    global _redis_client
    if _redis_client is None:
        import redis

        _redis_client = redis.Redis.from_url(
            settings.REDIS_URL, socket_timeout=2, socket_connect_timeout=1
        )
    return _redis_client


def notify_project(project_id: object) -> None:
    """Best-effort wake-up for open streams of ``project_id``."""
    if not project_id:
        return
    try:
        _redis().publish(_CHANNEL.format(project_id), "1")
    except Exception:  # noqa: BLE001 — never break a writer over a UI hint
        logger.debug("notify_project failed", exc_info=True)


def project_fingerprint(project_id: object) -> dict[str, str]:
    """One short token per UI data source; a change means "refetch that"."""
    from peon.projects.db import project_alias_or_default, using_project
    from peon.projects.models import (
        AssetGraph,
        Finding,
        Job,
        Objective,
        OperatorPrompt,
        Project,
    )

    def token(qs) -> str:
        agg = qs.aggregate(n=Count("pk"), t=Max("updated_at"))
        return f"{agg['n']}:{agg['t'].isoformat() if agg['t'] else ''}"

    alias = project_alias_or_default(project_id)
    out: dict[str, str] = {}
    project = Project.objects.filter(pk=project_id).values("status", "updated_at").first()
    out["project"] = f"{project['status']}:{project['updated_at']}" if project else "gone"
    with using_project(project_id):
        out["jobs"] = token(Job.objects.using(alias).filter(project_id=project_id))
        out["objectives"] = token(Objective.objects.using(alias).filter(project_id=project_id))
        out["findings"] = token(Finding.objects.using(alias).filter(project_id=project_id))
        out["assets"] = token(AssetGraph.objects.using(alias).filter(project_id=project_id))
        prompts = OperatorPrompt.objects.using(alias).filter(
            project_id=project_id, resolved_at__isnull=True
        )
        agg = prompts.aggregate(n=Count("pk"), t=Max("created_at"))
        out["prompts"] = f"{agg['n']}:{agg['t'] or ''}"
    return out


def _latest_message_id(project_id: object) -> int:
    from peon.projects.db import project_alias_or_default, using_project
    from peon.projects.models import StreamMessage

    alias = project_alias_or_default(project_id)
    with using_project(project_id):
        last = (
            StreamMessage.objects.using(alias)
            .filter(job__project_id=project_id)
            .aggregate(m=Max("id"))["m"]
        )
    return int(last or 0)


def _sse(event: str, data: Any, event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, separators=(',', ':'), cls=DjangoJSONEncoder)}\n\n"


def _start_cursor(request: HttpRequest, project_id: object) -> int:
    for raw in (request.headers.get("Last-Event-ID"), request.GET.get("after")):
        try:
            if raw not in (None, ""):
                return max(0, int(raw))
        except (TypeError, ValueError):
            continue
    # No cursor: the page bootstrap already rendered history; stream only new rows.
    return _latest_message_id(project_id)


class _Waker:
    """Blocks up to ``timeout`` for a project notification; sleeps without Redis."""

    def __init__(self, project_id: object):
        self._pubsub = None
        try:
            self._pubsub = _redis().pubsub(ignore_subscribe_messages=True)
            self._pubsub.subscribe(_CHANNEL.format(project_id))
        except Exception:  # noqa: BLE001
            logger.debug("live events: redis unavailable, falling back to ticks", exc_info=True)
            self._pubsub = None

    def wait(self, timeout: float) -> None:
        if self._pubsub is None:
            time.sleep(timeout)
            return
        try:
            if self._pubsub.get_message(timeout=timeout):
                # Drain a burst so one tick handles many inserts.
                while self._pubsub.get_message(timeout=0):
                    pass
        except Exception:  # noqa: BLE001
            self._pubsub = None
            time.sleep(timeout)

    def close(self) -> None:
        if self._pubsub is not None:
            try:
                self._pubsub.close()
            except Exception:  # noqa: BLE001
                pass


def _project_stream(project, cursor: int) -> Iterator[str]:
    from peon.projects.streaming import project_message_dicts

    waker = _Waker(project.pk)
    started = last_beat = time.monotonic()
    fingerprint = project_fingerprint(project.pk)
    try:
        yield "retry: 2000\n\n"
        yield _sse("ready", {"cursor": cursor}, cursor)
        while time.monotonic() - started < STREAM_LIFETIME_S:
            waker.wait(TICK_S)
            rows = project_message_dicts(project, after_id=cursor, limit=MESSAGE_BATCH)
            if rows:
                cursor = int(rows[-1]["id"])
                yield _sse("messages", rows, cursor)
            current = project_fingerprint(project.pk)
            changed = sorted(k for k, v in current.items() if fingerprint.get(k) != v)
            if changed:
                fingerprint = current
                yield _sse("invalidate", {"keys": changed})
            now = time.monotonic()
            if rows or changed:
                last_beat = now
            elif now - last_beat >= HEARTBEAT_S:
                last_beat = now
                yield ": keep-alive\n\n"
    finally:
        waker.close()
        close_old_connections()


@require_GET
def project_events(request: HttpRequest, pk) -> StreamingHttpResponse:
    from peon.projects.models import Project

    project = get_object_or_404(Project, pk=pk)
    return sse_response(request, _project_stream(project, _start_cursor(request, project.pk)))


def _pending_signature() -> tuple[str, list[dict[str, Any]]]:
    from peon.projects.hitl import pending_inputs_global

    items = pending_inputs_global()
    return "|".join(str(i.get("id")) for i in items), items


def _global_stream() -> Iterator[str]:
    started = last_beat = time.monotonic()
    signature = None
    try:
        yield "retry: 3000\n\n"
        while time.monotonic() - started < STREAM_LIFETIME_S:
            current, items = _pending_signature()
            if current != signature:
                signature = current
                last_beat = time.monotonic()
                yield _sse("pending", {"pending": items})
            elif time.monotonic() - last_beat >= HEARTBEAT_S:
                last_beat = time.monotonic()
                yield ": keep-alive\n\n"
            close_old_connections()
            time.sleep(3.0)
    finally:
        close_old_connections()


@require_GET
def global_events(request: HttpRequest) -> StreamingHttpResponse:
    """Cross-project operator prompts for the global HITL dialog and badge."""
    return sse_response(request, _global_stream())
