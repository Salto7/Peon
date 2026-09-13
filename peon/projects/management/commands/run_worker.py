"""Poll PENDING Jobs; optionally host the Unix stream socket (Compose worker)."""

from __future__ import annotations

import atexit
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from peon.projects.streaming import StreamSocketServer, handle_incoming_message

from peon.projects.worker import process_one


class Command(BaseCommand):
    help = (
        "Fallback poll worker when DRAMATIQ_ENABLED=false (tests / no Redis). "
        "Compose uses Dramatiq; optionally hosts STREAM_SOCKET_PATH for NDJSON lines."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--once",
            action="store_true",
            help="Process at most one job then exit",
        )
        parser.add_argument(
            "--poll",
            type=float,
            default=2.0,
            help="Seconds to sleep when queue is empty (default 2)",
        )
        parser.add_argument(
            "--no-stream-socket",
            action="store_true",
            help="Do not start the Unix stream listener in this process",
        )

    def handle(self, *args, **options) -> None:
        server = None
        if not options["no_stream_socket"]:

            path = getattr(settings, "STREAM_SOCKET_PATH", "/tmp/peon/stream.sock")
            server = StreamSocketServer(path, handle_incoming_message)
            server.start()
            atexit.register(server.stop)
            self.stdout.write(f"stream socket: {path}")

        once = bool(options["once"])
        poll = max(0.2, float(options["poll"]))
        self.stdout.write("worker started")
        while True:
            job = process_one()
            if job is not None:
                self.stdout.write(
                    self.style.NOTICE(
                        f"job {job.id} → {job.status}"
                        + (f" ({job.error})" if job.error else "")
                    )
                )
                if once:
                    if server:
                        server.stop()
                    return
                continue
            if once:
                self.stdout.write("idle")
                if server:
                    server.stop()
                return
            time.sleep(poll)
