"""Standalone Unix stream socket server (optional; worker embeds this by default)."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from peon.projects.streaming import run_until_signal, start_stream_server


class Command(BaseCommand):
    help = "Listen on STREAM_SOCKET_PATH for NDJSON job stream messages."

    def handle(self, *args, **options) -> None:
        server = start_stream_server()
        self.stdout.write(f"stream socket listening on {server.socket_path}")
        try:
            run_until_signal(on_stop=server.stop)
        finally:
            self.stdout.write("stream socket stopped")
