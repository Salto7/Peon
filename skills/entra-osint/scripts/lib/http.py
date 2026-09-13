"""HTTP helpers for Entra OpenID configuration lookups."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

_UA = "peon-entra-osint/1.0"


def fetch_json(url: str, *, timeout: int = 30) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _UA,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def try_fetch_json(url: str, *, timeout: int = 30) -> tuple[int, Any | None, str]:
    """Return (http_status_or_0, json_or_None, error_message)."""
    try:
        data = fetch_json(url, timeout=timeout)
        return 200, data, ""
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return int(exc.code), None, body or str(exc)
    except Exception as exc:
        return 0, None, str(exc)
