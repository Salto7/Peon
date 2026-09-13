---
name: mcp-bridge
description: Install/start/call MCP servers via Peon's MCP bridge (sandbox stdio). No hand-rolled JSON-RPC clients. Use when the operator provides MCP servers or mcp packages.
allowed-tools: mcp_ensure mcp_list_tools mcp_call mcp_close run_skill_script skills_list skill_view
metadata:
  version: 1.3.0
  category: platform
  tags: platform
  aliases: tool-mcp
  jobable: 'true'
  manually_created: 'true'
  protected: 'true'
  lifecycle: short
  max_iterations: '25'
---

## Goal
Use MCP when the operator pastes `mcpServers` / names an `npx`|`uvx` package. Prefer
direct `mcp_*` capabilities; scripts/run.py only for multi-step glue.

## Scripts
- scripts/run.py — multi-step glue only

## Bridge
1. `mcp_ensure(spec_json=…)` or `mcp_ensure(name=…)`
2. `mcp_list_tools(server)`
3. `mcp_call(server, tool, arguments_json)`
4. `mcp_close(server?)` optional

See [bridge reference](references/REFERENCE.md) for paths, secrets, and stdio spec.
