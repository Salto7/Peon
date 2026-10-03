---
name: network
description: >
  In-scope host and port scanning (nmap-focused). Use when objectives need
  network surface mapping under RoE; prefer --top-ports unless all-ports is
  explicit.
compatibility: Network scanning in the Peon sandbox (nmap)
metadata:
  author: peon
  version: "1.0.0"
  category: custom
  tags: network,scanner
  lifecycle: long
  max_iterations: "40"
  suggested_tools: nmap
  excluded_tools: ""
  aliases: network-scanner
---

## Mission
Enumerate authorized hosts/ports. Stay in-scope; record evidence and findings.

## Tool routing
Prefer `nmap` (suggested). Tag match includes `recon`/`network`/`scanner`.
Legacy: `run_skill_script("network-scanner", …)`.

## Boundaries
Passive DNS/OSINT → `recon`. HTTP app testing → `web`.

## Multi-agent
Prefer solo for a single scan brief. Use `propose_agents` /
`spawn_agent(..., link="peer")` only when the objective truly needs parallel
scopes (e.g. disjoint host sets).
