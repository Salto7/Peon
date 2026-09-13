---
name: domain-enum
description: Domain + subdomain engine used by the corporate recon workflow after its canonical handoff gate. Passive only. Use for passive domain/subdomain inventory after corporate OSINT handoff.
compatibility: Requires dnsx, dig, curl, whois
allowed-tools: sandbox_setup run_skill_script sandbox_status skills_list skill_view list_objectives update_objective_status record_finding list_findings
metadata:
  version: 3.3.0
  category: recon
  tags: OSINT
  requires_clis: dnsx, dig, curl, whois
  jobable: 'true'
  manually_created: 'true'
  lifecycle: long
---

## Goal
Passive inventory: apex domains from `ai-osint-subsidiaries`, then subdomains via `dnsx`.
Source of truth: `workspace/corp-entities.json`. Never parse `findings/*.md`.

OSINT chain: **`ai-osint-subsidiaries` → `domain-enum` → `entra-osint`**

## Scripts / assets
- scripts/run.py — project entry (`command=`)
- scripts/domain_enum.py — CLI implementation
- assets/wordlists/subdomains.txt — default dnsx wordlist

## Verify
```
run_skill_script("domain-enum", "scripts/run.py", command="--help")
```
CLIs (`dnsx`, `dig`, …) are provisioned from **tools/catalog** before the job runs.

## Workflow (mandatory)
1. Ensure corp/AI handoff:
```
run_skill_script("domain-enum", "scripts/run.py", command="from-corp --workspace workspace")
```
2. Full domain→subdomain flow:
```
run_skill_script("domain-enum", "scripts/run.py", command="pipeline --from-corp --out-dir workspace --md findings/domain-inventory.md")
```
3. Record notable subdomains with `record_finding`.

For corporate recon, invoke **`ai-osint-subsidiaries` `workflow`** first — do not manually
chain discovery and this skill.

See [reference](references/REFERENCE.md) for prerequisites, paths, subcommands, dnsx notes, and RoE.
