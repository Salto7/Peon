"""Standalone Unix stream socket server (optional; worker embeds this by default)."""

from __future__ import annotations

import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from peon.projects.streaming import StreamSocketServer, handle_incoming_message


class Command(BaseCommand):
    help = "Listen on STREAM_SOCKET_PATH for NDJSON job stream messages."

    def handle(self, *args, **options) -> None:
        path = getattr(settings, "STREAM_SOCKET_PATH", "/tmp/peon/stream.sock")
        server = StreamSocketServer(path, handle_incoming_message)
        server.start()
        self.stdout.write(f"stream socket listening on {path}")

        stop = False

        def _stop(*_args) -> None:
            nonlocal stop
            stop = True

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
        try:
            while not stop:
                time.sleep(0.5)
        finally:
            server.stop()
            self.stdout.write("stream socket stopped")
