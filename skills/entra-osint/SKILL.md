---
name: entra-osint
description: >
  Public Entra ID OSINT from project domains: query Azure OpenID configuration
  to resolve tenant IDs. Use after domain-enum once apex domains are inventoried.
allowed-tools: Bash Read
compatibility: Requires curl
---

## Goal
Resolve **Microsoft Entra ID (Azure AD) tenant IDs** for domains discovered during
the project. Public endpoints only — no authentication attacks.

**Run after `domain-enum`** (OSINT chain: **`ai-osint-subsidiaries` → `domain-enum` → `entra-osint`**).

## Scripts
- scripts/run.sh — entry (argv)
- scripts/entra_osint.py — CLI implementation

## Run
```
run_skill_script("entra-osint", "scripts/run.sh", args='["from-domains", "workspace/domains.txt", "--out", "workspace/entra-tenants.json"]')
```

See [reference](references/REFERENCE.md) for OpenID technique, paths, subcommands, workflow, and RoE.

## Collaboration & memory
- Before repeating a method, tool, or target, `recall_memory`; `save_memory` for durable lessons a later agent would otherwise rediscover.
- Report up: `send_message` findings, blockers, and scope changes to your parent / role agent — blockers and scope/RoE changes reach the governance PM.
- Stream, don't hoard: as you discover something a waiting agent needs, `list_team` then `send_message` it (to one agent, by its id) so it can start while you continue — batch per subject, do not wait for completion.
- `check_inbox` between steps; when you can only wait on another agent's output, `go_idle` (never poll).
