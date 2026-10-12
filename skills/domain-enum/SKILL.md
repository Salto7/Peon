---
name: domain-enum
description: Domain + subdomain engine used by the corporate recon workflow after its canonical handoff gate. Passive only. Use for passive domain/subdomain inventory after corporate OSINT handoff.
allowed-tools: Bash Read
compatibility: Requires dnsx, dig, curl, whois
---

## Goal
Passive inventory: apex domains from `ai-osint-subsidiaries`, then subdomains via `dnsx`.
Source of truth: `workspace/corp-entities.json`. Never parse `findings/*.md`.

OSINT chain: **`ai-osint-subsidiaries` → `domain-enum` → `entra-osint`**

## Scripts / assets
- scripts/run.sh — entry (argv)
- scripts/domain_enum.py — CLI implementation
- assets/wordlists/subdomains.txt — default dnsx wordlist

## Run
```
run_skill_script("domain-enum", "scripts/run.sh", args='["subdomains", "--from-corp", "--workspace", "workspace"]')
```
CLIs (`dnsx`, `dig`, …) are provisioned from **tools/catalog** before the job runs.

For corporate recon, invoke **`ai-osint-subsidiaries` `workflow`** first — do not manually
chain discovery and this skill.

See [reference](references/REFERENCE.md) for prerequisites, paths, subcommands, dnsx notes, and RoE.
