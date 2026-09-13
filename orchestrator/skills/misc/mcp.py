"""Minimal MCP helpers (no external mcp package required for local runs)."""

from __future__ import annotations

import re
from typing import Any

_MCP_HINT_RE = re.compile(
    r"(?is)\b(mcp\b|mcpServers|model\s+context\s+protocol|use\s+(?:this\s+)?mcp|"
    r"via\s+mcp|mcp\s+server)\b"
)


def looks_like_mcp_request(text: str) -> bool:
    return bool(_MCP_HINT_RE.search(text or ""))


def normalize_mcp_servers(raw: Any) -> list[dict[str, Any]]:
    """Normalize SKILL.md ``mcp`` metadata into a list of dict specs."""
    if not raw:
        return []
    if isinstance(raw, dict):
        items = [
            {"name": str(k), **(v if isinstance(v, dict) else {})}
            for k, v in raw.items()
        ]
    elif isinstance(raw, list):
        items = list(raw)
    else:
        items = [raw]
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, item in enumerate(items):
        if isinstance(item, str):
            name = item.strip()
            spec = {"name": name} if name else {}
        elif isinstance(item, dict):
            spec = dict(item)
            if not str(spec.get("name") or "").strip():
                spec["name"] = f"mcp-{idx + 1}"
        else:
            continue
        name = str(spec.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        spec["name"] = name
        out.append(spec)
    return out
