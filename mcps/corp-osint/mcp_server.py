#!/usr/bin/env python3
"""corp-OSINT MCP server (stdio JSON-RPC wrapper).

Host supplies reasoning; corp-OSINT runs public-source tools via
``corp_osint.runtime``. Protocol / tool schemas stay here.

Start::

    .venv/bin/python mcp_server.py
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

# Keep import-time chatter off the MCP stdout stream.
with contextlib.redirect_stdout(sys.stderr):
    import anyio
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    import mcp.types as types

    from corp_osint.config import STAGE_NAMES
    from corp_osint.log import setup_logging
    from corp_osint.plugins import PLUGIN_REGISTRY, list_plugins
    from corp_osint.runtime import run_pipeline, run_plugin

    setup_logging(verbosity=0)


# Session defaults for this MCP process (set via set_target).
TARGET: dict[str, Any] = {}


def _schema(
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


_SET_TARGET = types.Tool(
    name="set_target",
    description=(
        "Set the corporate OSINT target defaults for this MCP session. "
        "Call this once before run_pipeline or plugin tools; later calls may "
        "override any default. Public-source research only."
    ),
    inputSchema=_schema(
        {
            "company": {"type": "string", "description": "Legal company or brand name"},
            "domain": {"type": "string", "description": "Primary company domain"},
            "ticker": {
                "type": "string",
                "description": "Stock market ticker symbol, such as MSFT",
            },
            "cik": {"type": "string", "description": "SEC CIK"},
            "proxy": {
                "type": "string",
                "description": "Optional HTTP/HTTPS/SOCKS proxy URL",
            },
            "direct": {
                "type": "boolean",
                "description": "Ignore proxy environment variables",
            },
            "no_proxy": {
                "type": "string",
                "description": "Comma-separated hosts that bypass the proxy",
            },
        },
        required=["company"],
    ),
)

_STAGE_ENUM = list(STAGE_NAMES)

_PIPELINE_SCHEMA = _schema(
    {
        "company": {"type": "string", "description": "Company name; defaults to set_target"},
        "domain": {"type": "string", "description": "Primary domain"},
        "ticker": {"type": "string", "description": "Stock market ticker symbol"},
        "cik": {"type": "string"},
        "skip": {
            "type": "array",
            "items": {"type": "string", "enum": _STAGE_ENUM},
            "description": f"Stages to disable ({', '.join(STAGE_NAMES)})",
        },
        "only": {
            "type": "array",
            "items": {"type": "string", "enum": _STAGE_ENUM},
            "description": f"If set, run only these stages ({', '.join(STAGE_NAMES)})",
        },
        "proxy": {"type": "string"},
        "direct": {"type": "boolean"},
        "no_proxy": {"type": "string"},
    }
)

PLUGIN_SCHEMAS: dict[str, dict[str, Any]] = {
    "edgar": _schema(
        {
            "company": {"type": "string", "description": "Company name"},
            "ticker": {"type": "string", "description": "Stock market ticker symbol"},
            "cik": {"type": "string"},
        }
    ),
    "resolve_domains": _schema(
        {
            "name": {"type": "string", "description": "Company, subsidiary, or brand name"},
            "parent_domain": {"type": "string"},
        },
        required=["name"],
    ),
    "playwright_search": _schema(
        {
            "company": {"type": "string"},
            "domain": {"type": "string"},
            "engine": {"type": "string", "enum": ["bing", "duckduckgo"]},
            "max_queries": {"type": "integer", "minimum": 0, "default": 6},
        },
        required=["company"],
    ),
    "urlscan": _schema(
        {
            "domain": {"type": "string"},
            "company": {"type": "string"},
            "size": {"type": "integer", "minimum": 1, "maximum": 100, "default": 40},
        }
    ),
    "dnslytics_gtm": _schema(
        {
            "domains": {"type": "array", "items": {"type": "string"}},
            "domain": {"type": "string"},
            "max_domains": {"type": "integer", "minimum": 0, "default": 5},
            "max_gtm_reverse": {"type": "integer", "minimum": 0, "default": 5},
        }
    ),
    "rdap": _schema(
        {
            "domain": {"type": "string", "description": "Domain to enrich"},
            "force_whois": {"type": "boolean", "default": False},
        },
        required=["domain"],
    ),
    "azure_tenant": _schema(
        {
            "domain": {"type": "string"},
            "domains": {"type": "array", "items": {"type": "string"}},
            "expected_tenant_id": {"type": "string"},
            "max_domains": {"type": "integer", "minimum": 0, "default": 200},
            "concurrency": {"type": "integer", "minimum": 1, "default": 10},
        }
    ),
    "asn": _schema(
        {
            "domain": {"type": "string"},
            "company": {"type": "string"},
            "deep": {"type": "boolean", "default": False},
        },
        required=["domain"],
    ),
    "tenant_domains": _schema(
        {
            "domain": {"type": "string"},
            "tenant_id": {"type": "string"},
            "max_domains": {"type": "integer", "minimum": 0, "default": 200},
        }
    ),
    "dangling_check": _schema(
        {
            "domains": {"type": "array", "items": {"type": "string"}},
            "entra_verified_domains": {"type": "array", "items": {"type": "string"}},
            "tenant_id": {"type": "string"},
            "max_domains": {"type": "integer", "minimum": 0, "default": 200},
        }
    ),
}


def _context(arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Merge per-call inputs over session TARGET defaults."""
    merged = dict(TARGET)
    for key, value in (arguments or {}).items():
        if value is not None:
            merged[key] = value
    return merged


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _set_target(arguments: dict[str, Any]) -> str:
    company = str(arguments.get("company") or "").strip()
    if not company:
        return "set_target rejected: company is required; session unchanged."
    global TARGET
    TARGET = {
        key: value
        for key, value in arguments.items()
        if value is not None
        and key in {"company", "domain", "ticker", "cik", "proxy", "direct", "no_proxy"}
    }
    return (
        f"Target set: {TARGET.get('company')}"
        f" (domain={TARGET.get('domain') or 'unset'}, "
        f"stock market ticker={TARGET.get('ticker') or 'unset'}, "
        f"cik={TARGET.get('cik') or 'unset'})."
    )


async def _list_tools() -> list[types.Tool]:
    tools = [
        _SET_TARGET,
        types.Tool(
            name="run_pipeline",
            description=(
                "Run the complete corporate OSINT pipeline. Uses set_target defaults "
                "and returns compact summary plus raw structured evidence. "
                "Control stages with skip and/or only arrays "
                f"(stages: {', '.join(STAGE_NAMES)}). "
                "Progress and rate-limit backoff log to stderr."
            ),
            inputSchema=_PIPELINE_SCHEMA,
        ),
    ]
    descriptions = {row["name"]: row.get("description", "") for row in list_plugins()}
    for name in sorted(PLUGIN_REGISTRY):
        tools.append(
            types.Tool(
                name=name,
                description=descriptions.get(name) or f"Run the {name} OSINT plugin",
                inputSchema=PLUGIN_SCHEMAS[name],
            )
        )
    return tools


async def _call_tool(name: str, arguments: dict[str, Any] | None):
    args = arguments or {}
    if name == "set_target":
        return [types.TextContent(type="text", text=_set_target(args))]
    values = _context(args)
    if name == "run_pipeline":
        result = await run_pipeline(values)
    elif name in PLUGIN_REGISTRY:
        result = await run_plugin(name, values)
    else:
        return [types.TextContent(type="text", text=f"Unknown tool: {name}")]
    return [types.TextContent(type="text", text=_json_text(result))]


async def _handle_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
    return types.ListToolsResult(tools=await _list_tools())


async def _handle_call_tool(ctx: Any, params: Any) -> types.CallToolResult:
    try:
        content = await _call_tool(params.name, params.arguments or {})
        return types.CallToolResult(content=content)
    except Exception as exc:  # noqa: BLE001
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"{type(exc).__name__}: {exc}")],
            isError=True,
        )


server = Server(
    "corp-osint",
    version="1.0.0",
    instructions=(
        "Use set_target first for a company and optional primary domain, stock market ticker symbol, or CIK. "
        "Then use run_pipeline for a complete passive public-source investigation or call "
        "one plugin for a focused lookup. "
        f"run_pipeline accepts skip/only stage lists ({', '.join(STAGE_NAMES)}). "
        "Treat results as evidence and distinguish verified domains from candidates."
    ),
    on_list_tools=_handle_list_tools,
    on_call_tool=_handle_call_tool,
)


async def _main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        sys.stdout = sys.stderr
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    """Sync entrypoint for ``uv run`` / ``[project.scripts] corp-osint-mcp``."""
    anyio.run(_main)


if __name__ == "__main__":
    main()
