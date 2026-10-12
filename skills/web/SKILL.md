---
name: web
description: Web app and HTTP API assessment within RoE. Maps live HTTP surface then probes auth and inputs with evidence-backed findings. Use for web/appsec objectives; not for DNS-only OSINT or binary analysis.
allowed-tools: Bash Read
compatibility: Peon sandboxed web assessment under RoE (active testing needs in-scope targets)
---

## Mission
Assess in-scope web apps/APIs. Prefer map-then-test. Findings need evidence.

## Tool routing
Eligible CLIs match tags `web` (and suggested `httpx`). Use only what the
objective needs — e.g. httpx for probe; feroxbuster/dalfox/sqlmap when tagged
`web` and the objective calls for them. Provision on first use.
HTTP probing skill: `http-prober`.

## Multi-agent
Run as a **skill worker** under the governing expert (`spawn_work` with
`skill_ids="web"` / `http-prober`). Cover the URL set in the brief as given —
do not invent parallel slices unless the operator asks. Coordinate via
`list_work` / `send_agent_message`. Do not invent role ids.

## Outputs
Evidence under `workspace/`; record findings; update objective when done/blocked.

## Boundaries
DNS/OSINT → `recon`. Host scanning → `network`. Final report → `analyzer`.
