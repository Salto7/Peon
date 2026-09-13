#!/usr/bin/env python3
# Pre-wired by mcp-bridge — LLM writes ONLY the body below the marker.
# Do NOT hand-roll MCP JSON-RPC. Do NOT put secrets in this file.
from skill_entry import main, run_glue

_IMPORTS = (
    "from orchestrator_tools import stream, print, "
    "mcp_ensure, mcp_list_tools, mcp_call, mcp_close"
)

# mcp_ensure(spec_json='{"name":"demo","command":"npx","args":["-y","…-mcp"]}')
# tools = mcp_list_tools("demo")
# result = mcp_call("demo", "tool_name", arguments_json="{}")
# mcp_close("demo")
# stream("log", str(result))
# print(result)

if __name__ == "__main__":
    main(lambda: run_glue(__file__, imports=_IMPORTS))

# --- LLM_BODY ---
