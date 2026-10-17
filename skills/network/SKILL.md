---
name: network
description: In-scope host and port scanning (nmap-focused). Use when objectives need network surface mapping under RoE; prefer --top-ports unless all-ports is explicit.
allowed-tools: Bash Read
compatibility: Network scanning in the Peon sandbox (nmap)
---

## Mission
Enumerate authorized hosts/ports. Stay in-scope; record evidence and findings.

## Tool routing
Prefer `nmap` (suggested). Tag match includes `recon`/`network`/`scanner`.
Scanner skill: `network-scanner`.

## Boundaries
Passive DNS/OSINT → `recon`. HTTP app testing → `web`.

## Multi-agent
Scan the authorized CIDR/host list **as given** — do not subdivide into
`scope_slice` workers unless the operator asks. Post-scan HTTP/LDAP/SMB
follow-ons: spawn `http-prober`, `web`, or `network-service-recon` under this
expert — do not bounce to the PM.

## Collaboration & memory
- Before repeating a method, tool, or target, `recall_memory`; `save_memory` for durable lessons a later agent would otherwise rediscover.
- Report up: `send_message` findings, blockers, and scope changes to your parent / role agent — blockers and scope/RoE changes reach the governance PM.
- Stream, don't hoard: as you discover something a waiting agent needs, `list_team` then `send_message` it (to one agent, by its id) so it can start while you continue — batch per subject, do not wait for completion.
- `check_inbox` between steps; when you can only wait on another agent's output, `go_idle` (never poll).
