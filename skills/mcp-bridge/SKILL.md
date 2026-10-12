---
name: mcp-bridge
description: Install/start/call MCP servers via Peon's MCP bridge (sandbox stdio). No hand-rolled JSON-RPC clients. Use when the operator provides MCP servers or mcp packages.
allowed-tools: Bash Read
---

## Goal
Use MCP when the operator pastes `mcpServers` / names an `npx`|`uvx` package. Prefer
`run_skill_script` for multi-step glue via `scripts/run.sh`.

## Scripts
- scripts/run.sh — multi-step glue entry (argv)

See [bridge reference](references/REFERENCE.md) for paths, secrets, and stdio spec.

## Run
```
run_skill_script("mcp-bridge", "scripts/run.sh", args='[]')
```
