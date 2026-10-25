#!/usr/bin/env python3
"""
AdStrike MCP server — exposes AdStrike's AD-attack tools over the Model Context
Protocol so a host (Claude Code, Cursor, Claude Desktop, ...) drives the
engagement with its OWN subscription. On this path AdStrike runs no Ollama and
needs no Anthropic API key: the host is the brain, AdStrike is the toolbox.

The host LLM supplies only tool-specific arguments. AdStrike injects the
authoritative target + credentials from the session — set once via the
`set_engagement` tool — into every subsequent call (see _sanitize_tool_inputs),
so the model never has to know or pass the password. Fully target-agnostic.

Launch (system python3 has both `mcp` and AdStrike's deps):
    python3 mcp_server.py            # speaks JSON-RPC over stdio

Register in Claude Code (.mcp.json) — see docs/mcp.md.
"""
import os
import sys
import contextlib

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
# A host may launch us from any cwd. Some tool command strings are cwd-relative
# (e.g. "output/agent_runtime/..."), so anchor to the repo root the way the
# standalone agent runs — keeps loot/runtime files in the expected place.
os.chdir(_ROOT)

# AdStrike tool code prints colorful status to stdout, but an MCP stdio server
# speaks JSON-RPC over stdout — any stray byte corrupts the protocol. Keep
# import-time chatter off the protocol stream (restored after the import), then
# at runtime route every print() to stderr. stdio_server() binds the *real*
# stdout it captures at context entry, so reassigning sys.stdout afterwards only
# affects subsequent print() calls, never the protocol.
with contextlib.redirect_stdout(sys.stderr):
    from modules.agent._core import (
        TOOLS,
        TOOL_MAP,
        dispatch_tool,
        SESSION,
        save_session,
    )
    # Same SESSION dict the tools mutate. These keep set_engagement honest when
    # the host switches targets mid-session: reset_session_for_target_change wipes
    # per-engagement state (loot, findings, agent_intel, Kerberos ccache, base_dn,
    # …) when the target/domain changes; _refresh_derived rebuilds domain_user /
    # upn / base_dn from the new domain+username.
    from config.settings import reset_session_for_target_change, _refresh_derived

import anyio
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

server = Server("adstrike")

_SET_ENGAGEMENT = types.Tool(
    name="set_engagement",
    description=(
        "Set the engagement target and credentials ONCE before running any attack "
        "tool. AdStrike injects these into every later tool call, so you never pass "
        "the password again. Provide password OR nt_hash."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "dc_ip":    {"type": "string", "description": "Domain Controller IP"},
            "domain":   {"type": "string", "description": "AD domain FQDN, e.g. corp.local"},
            "username": {"type": "string", "description": "Authenticating user (sAMAccountName)"},
            "password": {"type": "string", "description": "User password (omit if using nt_hash)"},
            "nt_hash":  {"type": "string", "description": "NTLM hash (omit if using password)"},
            "dc_fqdn":  {"type": "string", "description": "DC FQDN, e.g. dc1.corp.local (optional)"},
        },
        "required": ["dc_ip", "domain", "username"],
    },
)


@server.list_tools()
async def _list_tools():
    """Expose set_engagement plus every executable AdStrike tool, reusing the
    same schemas the standalone agent uses (single source of truth)."""
    tools = [_SET_ENGAGEMENT]
    for t in TOOLS:
        if t["name"] in TOOL_MAP:
            tools.append(
                types.Tool(
                    name=t["name"],
                    description=t.get("description", ""),
                    inputSchema=t["input_schema"],
                )
            )
    return tools


def _set_engagement(args: dict) -> str:
    dc_ip    = str(args.get("dc_ip") or "").strip()
    domain   = str(args.get("domain") or "").strip()
    username = str(args.get("username") or "").strip()
    password = args.get("password") or ""
    nt_hash  = args.get("nt_hash") or ""
    dc_fqdn  = str(args.get("dc_fqdn") or "").strip()

    # validate_input=False means the protocol layer won't enforce the schema for
    # us, so guard required fields + the password/nt_hash XOR here. Reject and
    # leave the session untouched rather than silently leaning on stale state.
    missing = [k for k, v in (("dc_ip", dc_ip), ("domain", domain),
                              ("username", username)) if not v]
    if missing:
        return ("set_engagement rejected: missing required field(s): "
                f"{', '.join(missing)}. Session unchanged.")
    if password and nt_hash:
        return ("set_engagement rejected: provide password OR nt_hash, not both. "
                "Session unchanged.")

    # Switching target/domain must not carry the previous engagement's loot,
    # findings, agent_intel, Kerberos ccache, or base_dn forward. This sets the
    # new dc_ip + domain and wipes per-engagement state when they changed.
    prev_username = str(SESSION.get("username", "")).strip()
    changed = reset_session_for_target_change(dc_ip=dc_ip, domain=domain)
    # A credential belongs to one principal. If the identity changes — different
    # target OR different username — a credential from the old principal must not
    # ride along to the new one.
    identity_changed = changed or (
        bool(prev_username) and username.lower() != prev_username.lower()
    )

    SESSION["username"] = username
    if dc_fqdn:
        SESSION["dc_fqdn"] = dc_fqdn

    # Enforce the XOR on stored state too: exactly one credential survives, so a
    # password from a previous engagement can't linger behind a new nt_hash (or
    # vice versa). When the identity changes and no credential is supplied, clear
    # both so the new principal starts from a clean no-cred footing instead of
    # inheriting the previous user's secret.
    if password:
        SESSION["password"], SESSION["nt_hash"] = password, ""
    elif nt_hash:
        SESSION["password"], SESSION["nt_hash"] = "", nt_hash
    elif identity_changed:
        SESSION["password"], SESSION["nt_hash"] = "", ""

    # Rebuild domain_user / upn / base_dn now that domain AND username are set
    # (reset_session_for_target_change refreshed before username was applied).
    # _refresh_derived only fills base_dn when it's empty, and the reset path can
    # leave a previous domain's base_dn behind — clear it so it re-derives from
    # the current domain.
    SESSION["base_dn"] = ""
    _refresh_derived()
    with contextlib.suppress(Exception):
        save_session()

    ident = f"{SESSION.get('username', '?')}@{SESSION.get('domain', '?')} → {SESSION.get('dc_ip', '?')}"
    auth = "password" if SESSION.get("password") else ("nt_hash" if SESSION.get("nt_hash") else "none")
    if changed:
        note = " stale engagement state cleared;"
    elif identity_changed and auth == "none":
        note = " previous user's credential cleared (new user, none supplied);"
    else:
        note = ""
    return f"Engagement set: {ident} (auth={auth}).{note} Run nmap_scan / enumerate_ldap next."


# validate_input=False: AdStrike's own _sanitize_tool_inputs (inside dispatch_tool)
# is the single validation authority — it strips unknown keys, injects session
# credentials, and reports missing required inputs gracefully. Double-validating
# here would reject otherwise-handleable calls.
@server.call_tool(validate_input=False)
async def _call_tool(name: str, arguments: dict | None):
    args = arguments or {}
    if name == "set_engagement":
        return [types.TextContent(type="text", text=_set_engagement(args))]
    if name not in TOOL_MAP:
        return [types.TextContent(type="text", text=f"Unknown tool: {name}")]
    # Tools shell out to nmap/certipy/evil-winrm/etc. (blocking). Offload to a
    # worker thread so the asyncio event loop stays responsive for the protocol.
    out = await anyio.to_thread.run_sync(dispatch_tool, name, args)
    return [types.TextContent(type="text", text=str(out))]


async def _main():
    async with stdio_server() as (read_stream, write_stream):
        # Protocol streams are already bound to the real stdout; from here on send
        # all tool/print chatter to stderr so it can't corrupt JSON-RPC frames.
        sys.stdout = sys.stderr
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    anyio.run(_main)
