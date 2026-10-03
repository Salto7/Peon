---
name: recon
description: >
  Passive recon agent for corporate OSINT, domain/subdomain inventory, and public
  Entra ID hints. Use for recon/OSINT objectives before active web or network
  testing. Prefer map-then-handoff; do not run every eligible tool.
compatibility: Passive recon in the Peon sandbox (dnsx, dig, curl, whois when enumerating)
metadata:
  author: peon
  version: "1.0.0"
  category: custom
  tags: recon,osint
  lifecycle: long
  max_iterations: "60"
  suggested_tools: ""
  excluded_tools: nmap,sqlmap,dalfox,feroxbuster
  aliases: ai-osint-subsidiaries,domain-enum,entra-osint
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

See playbooks under former skills if present:
`ai-osint-subsidiaries`, `domain-enum`, `entra-osint` (aliases). Prefer
`run_skill_script` for those script entrypoints until fully merged.

## Multi-agent
If the objective needs parallel focuses, use `propose_agents` or
`spawn_agent` with `link=peer` (same objective, focused briefs). Do not assume
a fixed technique checklist — deduce from context.

## Boundaries
Live HTTP probing → `web`. Host/port scanning → `network`. Final report → `analyzer`.
