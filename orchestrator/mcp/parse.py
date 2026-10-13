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
    cwd: str
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
        seen: set[str] = set()
        for name, body in servers.items():
            if not isinstance(body, dict):
                raise McpParseError(f"mcpServers.{name} must be an object")
            merged = dict(body)
            merged.setdefault("name", name)
            spec = _one(merged)
            if spec.mcp_id in seen:
                raise McpParseError(f"duplicate server {spec.mcp_id!r}")
            seen.add(spec.mcp_id)
            specs.append(spec)
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
    if not name or len(name) > 128 or not SKILL_NAME_RE.match(name):
        raise McpParseError(
            "server name is required and must be kebab-case "
            f"(got {name!r})"
        )
    transport = str(raw.get("transport") or raw.get("type") or "stdio").strip().lower()
    if transport in {"sse", "http", "streamable-http", "websocket"}:
        raise McpParseError(
            f"transport {transport!r} is not supported; the sandbox runs stdio servers"
        )
    if transport != "stdio":
        raise McpParseError("only transport stdio is supported")
    command, args = _command(raw)
    description = str(raw.get("description") or "").strip()
    if len(description) > 4000:
        raise McpParseError("description is too long")
    install = _install(raw.get("install"))
    return McpSpec(
        mcp_id=name,
        description=description,
        transport=transport,
        command=command,
        args=args,
        cwd=_cwd(raw.get("cwd")),
        install=install,
        env_schema=_env_schema(raw.get("env_schema") or raw.get("env")),
    )


def _command(raw: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    command_raw = raw.get("command")
    args_raw = raw.get("args") if raw.get("args") is not None else []
    if not isinstance(args_raw, list) or not all(isinstance(item, str) for item in args_raw):
        raise McpParseError("args must be a list of strings")
    if isinstance(command_raw, list):
        if not command_raw or not all(isinstance(item, str) for item in command_raw):
            raise McpParseError("command list must be non-empty strings")
        command = command_raw[0].strip()
        args = tuple(item for item in (*command_raw[1:], *args_raw))
    elif isinstance(command_raw, str):
        command = command_raw.strip()
        args = tuple(args_raw)
    else:
        raise McpParseError("command is required")
    if not command or len(command) > 256 or any(ch in command for ch in "\n\r\x00"):
        raise McpParseError("command is required and must be a single line")
    if len(args) > 64:
        raise McpParseError("too many args")
    if any(any(ch in item for ch in "\n\r\x00") or len(item) > 4096 for item in args):
        raise McpParseError("args must be single-line strings")
    return command, args


def _cwd(raw: Any) -> str:
    if raw in (None, "", "."):
        return ""
    text = str(raw).strip().replace("\\", "/")
    if text.startswith("/") or text.startswith("~"):
        raise McpParseError("cwd must be a relative path inside the server directory")
    parts = [part for part in text.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise McpParseError("cwd must be a relative path inside the server directory")
    if len("/".join(parts)) > 256:
        raise McpParseError("cwd is too long")
    return "/".join(parts)


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
    """Names only. A mapping may declare names, but its values are never kept."""
    if raw in (None, "", []):
        return ()
    if isinstance(raw, dict):
        filled = [
            str(key)
            for key, value in raw.items()
            if value is not None and str(value).strip()
        ]
        if filled:
            raise McpParseError(
                "env values are not stored in the catalog "
                f"({', '.join(filled)}). Declare the names and set values on the project."
            )
        raw = list(raw.keys())
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
