---
name: mcp-bridge
description: Install/start/call MCP servers via Peon's MCP bridge (sandbox stdio). No hand-rolled JSON-RPC clients. Use when the operator provides MCP servers or mcp packages.
allowed-tools: run_skill_script skills_list skill_view
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
`run_skill_script` for multi-step glue via scripts/run.py.

## Scripts
- scripts/run.py — multi-step glue only

See [bridge reference](references/REFERENCE.md) for paths, secrets, and stdio spec.
