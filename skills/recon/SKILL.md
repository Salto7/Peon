---
name: recon
description: Passive recon agent for corporate OSINT, domain/subdomain inventory, and public Entra ID hints. Use for recon/OSINT objectives before active web or network testing. Prefer map-then-handoff; do not run every eligible tool.
allowed-tools: Bash Read
compatibility: Passive recon in the Peon sandbox (dnsx, dig, curl, whois when enumerating)
---

## Mission
Build a passive inventory of organizations, domains, and related identity
surfaces in scope. Record evidence under `workspace/` and findings for
discoveries (not run status).

## Tool routing
Eligible catalog CLIs match skill tags (`recon`, `osint`) unless excluded.
Typical flow:

1. Corporate / subsidiary discovery → handoff gate for domain work
2. Domain + subdomain inventory (`dnsx`, dig, whois)
3. Optional Entra/OpenID tenant hints from apex domains (`curl`)

Chain skills: `ai-osint-subsidiaries` → `domain-enum` → `entra-osint`.
Prefer `run_skill_script` / `load_skill` for those packs.

## Multi-agent
If the objective needs parallel focuses, use `propose_work` or
`spawn_work` with `link=peer` (same objective, focused briefs). Do not assume
a fixed technique checklist — deduce from context.

## Boundaries
Live HTTP probing → `web`. Host/port scanning → `network`. Final report → `analyzer`.

## Collaboration & memory
- Before repeating a method, tool, or target, `recall_memory`; `save_memory` for durable lessons a later agent would otherwise rediscover.
- Report up: `send_message` findings, blockers, and scope changes to your parent / role agent — blockers and scope/RoE changes reach the governance PM.
- Stream, don't hoard: as you discover something a waiting agent needs, `list_team` then `send_message` it (to one agent, by its id) so it can start while you continue — batch per subject, do not wait for completion.
- `check_inbox` between steps; when you can only wait on another agent's output, `go_idle` (never poll).
