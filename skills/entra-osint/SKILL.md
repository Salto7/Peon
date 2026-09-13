---
name: entra-osint
description: 'Public Entra ID OSINT from project domains: query Azure OpenID configuration to resolve tenant IDs. Use after domain-enum once apex domains are inventoried'
compatibility: Requires curl
allowed-tools: sandbox_setup run_skill_script skills_list skill_view list_objectives update_objective_status record_finding list_findings
metadata:
  version: 1.0.0
  category: recon
  tags: OSINT, EntraID, Azure
  requires_clis: curl
  jobable: 'true'
  manually_created: 'true'
  lifecycle: long
---

## Goal
Resolve **Microsoft Entra ID (Azure AD) tenant IDs** for domains discovered during
the project. Passive public endpoints only — no authentication attacks.

**Run after `domain-enum`** (OSINT chain: **`ai-osint-subsidiaries` → `domain-enum` → `entra-osint`**).

## Scripts
- scripts/run.py — project entry (`command=`)
- scripts/entra_osint.py — CLI implementation

## Verify / run
```
run_skill_script("entra-osint", "scripts/run.py", command="--help")
run_skill_script("entra-osint", "scripts/run.py", command="tenant acme.com")
run_skill_script("entra-osint", "scripts/run.py", command="from-inventory workspace/domain-inventory.json --out workspace/entra-tenants.json --md findings/entra-osint.md")
```

Always use scripts/run.py with `command=` (sandbox spine).

See [reference](references/REFERENCE.md) for OpenID technique, paths, subcommands, workflow, and RoE.
