"""Parse MCP server documents. No database and no process starts.

Accepts one server object or an ``mcpServers`` map. ``upsert`` in
``PlatformCatalog`` runs this before every create or replace.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from orchestrator.skills.common import SKILL_NAME_RE

_SECRET_KEYS = frozenset(
    {
        "token",
        "password",
        "secret",
        "api_key",
        "apikey",
        "authorization",
        "access_token",
        "refresh_token",
    }
)
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class McpSpec:
    mcp_id: str
    description: str
    transport: str
    command: str
    args: tuple[str, ...]
    install: dict[str, Any] | None
    env_schema: tuple[tuple[str, bool], ...]


class McpParseError(ValueError):
    """The document is not a usable MCP server spec."""


def parse_mcp_document(document: dict[str, Any] | str | list[Any]) -> tuple[McpSpec, ...]:
    """Return one spec per server. Raises ``McpParseError`` on invalid input."""
    data = _load(document)
    _reject_secrets(data)
    if isinstance(data, dict) and isinstance(data.get("mcpServers"), dict):
        servers = data["mcpServers"]
        specs = []
        for name, body in servers.items():
            if not isinstance(body, dict):
                raise McpParseError(f"mcpServers.{name} must be an object")
            merged = dict(body)
            merged.setdefault("name", name)
            specs.append(_one(merged))
        if not specs:
            raise McpParseError("mcpServers is empty")
        return tuple(specs)
    if isinstance(data, dict):
        return (_one(data),)
    raise McpParseError("MCP document must be an object or mcpServers map")


def _load(document: dict[str, Any] | str | list[Any]) -> Any:
    if isinstance(document, str):
        text = document.strip()
        if not text:
            raise McpParseError("empty MCP document")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise McpParseError(f"MCP document is not JSON: {exc}") from exc
    return document


def _one(raw: dict[str, Any]) -> McpSpec:
    name = str(raw.get("name") or raw.get("mcp_id") or raw.get("id") or "").strip().lower()
    if not name or not SKILL_NAME_RE.match(name):
        raise McpParseError(
            "server name is required and must be kebab-case "
            f"(got {name!r})"
        )
    transport = str(raw.get("transport") or "stdio").strip().lower()
    if transport != "stdio":
        raise McpParseError("only transport stdio is supported")
    command = str(raw.get("command") or "").strip()
    if not command or any(ch in command for ch in "\n\r\x00"):
        raise McpParseError("command is required")
    args_raw = raw.get("args") or []
    if not isinstance(args_raw, list) or not all(isinstance(item, str) for item in args_raw):
        raise McpParseError("args must be a list of strings")
    if any(any(ch in item for ch in "\n\r\x00") for item in args_raw):
        raise McpParseError("args must be single-line strings")
    description = str(raw.get("description") or "").strip()
    install = _install(raw.get("install"))
    return McpSpec(
        mcp_id=name,
        description=description,
        transport=transport,
        command=command,
        args=tuple(args_raw),
        install=install,
        env_schema=_env_schema(raw.get("env_schema") or raw.get("env")),
    )


def _install(raw: Any) -> dict[str, Any] | None:
    if raw in (None, "", {}):
        return None
    if not isinstance(raw, dict):
        raise McpParseError("install must be an object")
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in {"pip", "npm", "none"}:
        raise McpParseError("install.kind must be pip, npm, or none")
    if kind == "none":
        return None
    manifest = str(raw.get("manifest") or "").strip() or (
        "requirements.txt" if kind == "pip" else "package.json"
    )
    if ".." in manifest.split("/") or manifest.startswith("/"):
        raise McpParseError("install.manifest must be a relative file name")
    return {"kind": kind, "manifest": manifest}


def _env_schema(raw: Any) -> tuple[tuple[str, bool], ...]:
    """Names only. A mapping of name to value is rejected (that is a secret)."""
    if raw in (None, "", []):
        return ()
    if isinstance(raw, dict):
        raise McpParseError(
            "env must be a list of names, not values. "
            "Set values on the project, not in the catalog."
        )
    if not isinstance(raw, list):
        raise McpParseError("env_schema must be a list")
    out: list[tuple[str, bool]] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            name, required = item.strip(), True
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            required = bool(item.get("required", True))
            if any(k for k in item if k not in {"name", "required"}):
                raise McpParseError("env_schema entries only allow name and required")
        else:
            raise McpParseError("env_schema entries must be strings or objects")
        if not _ENV_NAME.match(name):
            raise McpParseError(f"invalid env name {name!r}")
        if name in seen:
            raise McpParseError(f"duplicate env name {name!r}")
        seen.add(name)
        out.append((name, required))
    return tuple(out)


def _reject_secrets(value: Any, path: str = "") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_s = str(key).strip().lower().replace("-", "_")
            here = f"{path}{key}."
            if key_s in _SECRET_KEYS and isinstance(item, str) and item.strip():
                raise McpParseError(f"do not put secret values in the catalog ({path}{key})")
            _reject_secrets(item, here)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secrets(item, f"{path}{index}.")
