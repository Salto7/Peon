"""Poll PENDING Jobs; optionally host stream + RPC Unix sockets (Compose worker)."""

from __future__ import annotations

import atexit
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from peon.projects.rpc_bridge import start_peon_rpc_server
from peon.projects.streaming import start_stream_server
from peon.projects.worker import process_one


class Command(BaseCommand):
    help = (
        "Fallback poll worker when DRAMATIQ_ENABLED=false (tests / no Redis). "
        "Compose uses Dramatiq; optionally hosts STREAM_SOCKET_PATH + RPC_SOCKET_PATH."
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
        parser.add_argument(
            "--no-rpc-socket",
            action="store_true",
            help="Do not start the Unix RPC listener in this process",
        )

    def handle(self, *args, **options) -> None:
        stream_server = None
        rpc_server = None
        if not options["no_stream_socket"]:
            stream_server = start_stream_server()
            atexit.register(stream_server.stop)
            self.stdout.write(f"stream socket: {stream_server.socket_path}")

        if not options["no_rpc_socket"]:
            rpc_path = getattr(settings, "RPC_SOCKET_PATH", "/tmp/peon/rpc.sock")
            rpc_server = start_peon_rpc_server()
            atexit.register(rpc_server.stop)
            self.stdout.write(f"RPC socket: {rpc_path}")

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
                    if stream_server:
                        stream_server.stop()
                    if rpc_server:
                        rpc_server.stop()
                    return
                continue
            if once:
                self.stdout.write("idle")
                if stream_server:
                    stream_server.stop()
                if rpc_server:
                    rpc_server.stop()
                return
            time.sleep(poll)
