"""Minimal HTTP helpers for domain-enum discovery."""
from __future__ import annotations

import json
import urllib.request
from typing import Any

_UA = "peon-domain-enum/2.1"


def fetch(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 60,
) -> str:
    hdrs = {
        "User-Agent": _UA,
        "Accept": "text/html,application/json,*/*",
    }
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read().decode("utf-8", errors="replace")


def fetch_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 60,
) -> Any:
    return json.loads(fetch(url, headers=headers, timeout=timeout))
