"""Host RPC bridge — start RpcServer from Django settings."""

from __future__ import annotations

import logging

from django.conf import settings

from orchestrator.runtime.rpc import RpcServer

logger = logging.getLogger(__name__)


def start_peon_rpc_server() -> RpcServer:
    """Listen on ``RPC_SOCKET_PATH`` with the shared ``RPC_TOKEN``."""
    path = str(getattr(settings, "RPC_SOCKET_PATH", "") or "").strip()
    if not path:
        raise RuntimeError("RPC_SOCKET_PATH is not configured")
    token = str(getattr(settings, "RPC_TOKEN", "") or "").strip() or None
    server = RpcServer.start(path, token=token)
    logger.info("RPC Unix socket listening on %s", path)
    return server
