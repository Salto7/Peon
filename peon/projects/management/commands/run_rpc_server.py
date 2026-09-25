"""Standalone Unix RPC socket server (skill_view / MCP host bridge)."""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from peon.projects.rpc_bridge import start_peon_rpc_server
from peon.projects.streaming import run_until_signal


class Command(BaseCommand):
    help = "Listen on RPC_SOCKET_PATH for sandboxed skill helper RPC calls."

    def handle(self, *args, **options) -> None:
        path = getattr(settings, "RPC_SOCKET_PATH", "/tmp/peon/rpc.sock")
        server = start_peon_rpc_server()
        self.stdout.write(f"RPC socket listening on {path}")
        try:
            run_until_signal(on_stop=server.stop)
        finally:
            self.stdout.write("RPC socket stopped")
