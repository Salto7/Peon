---
name: network-scanner
description: nmap in the project sandbox (recon). In-scope hosts only; prefer --top-ports unless all-ports is explicit. Use when scanning in-scope hosts/ports with nmap.
compatibility: Requires nmap
allowed-tools: sandbox_setup run_skill_script sandbox_status skills_list skill_view list_objectives update_objective_status record_finding list_findings
metadata:
  version: 1.5.0
  category: recon
  tags: scanner,network,recon
  aliases: tool-nmap
  requires_clis: nmap
  jobable: 'true'
  lifecycle: long
---

## Goal
Authorized **nmap** scans. Driven under **project-manager** / project plan when present.

## Scripts
- scripts/run.py — project entry

## Run
```
sandbox_setup()
run_skill_script("network-scanner", "scripts/run.py", command="nmap -sT --top-ports 100 <host>")
```
Verify: `nmap --version` (CLI provisioned from tools/catalog).

See [usage notes](references/USAGE.md) for paths, CLIs, and RoE.
