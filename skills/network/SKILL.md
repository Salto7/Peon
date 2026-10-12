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
