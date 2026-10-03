---
name: web
description: >
  Web app and HTTP API assessment within RoE. Maps live HTTP surface then probes
  auth and inputs with evidence-backed findings. Use for web/appsec objectives;
  not for DNS-only OSINT or binary analysis.
compatibility: Peon sandboxed web assessment under RoE (active testing needs in-scope targets)
metadata:
  author: peon
  version: "1.0.0"
  category: custom
  tags: web,http,appsec
  lifecycle: long
  max_iterations: "60"
  suggested_tools: httpx
  excluded_tools: ""
  aliases: http-prober
---

## Mission
Assess in-scope web apps/APIs. Prefer map-then-test. Findings need evidence.

## Tool routing
Eligible CLIs match tags `web` (and suggested `httpx`). Use only what the
objective needs — e.g. httpx for probe; feroxbuster/dalfox/sqlmap when tagged
`web` and the objective calls for them. Provision on first use.

Legacy entrypoint: `run_skill_script("http-prober", …)` until scripts are merged.

## Multi-agent
One objective may be fulfilled by several Jobs. If context warrants parallel
specialists, call `propose_agents` (LLM deduces workstreams — no fixed technique
menu) or `spawn_agent(..., link="peer")` with a focused brief. Coordinate via
`list_agents` / `send_agent_message`. Solo is fine when one agent is enough.

## Outputs
Evidence under `workspace/`; record findings; update objective when done/blocked.

## Boundaries
DNS/OSINT → `recon`. Host scanning → `network`. Final report → `analyzer`.
