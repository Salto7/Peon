---
name: http-prober
description: httpx HTTP probing in the sandbox (web/recon). In-scope URLs only. Use when probing live HTTP services with httpx.
compatibility: Requires httpx
allowed-tools: sandbox_setup run_skill_script skills_list skill_view list_objectives update_objective_status record_finding list_findings
metadata:
  version: 1.3.0
  category: recon
  aliases: tool-httpx
  tags: web-pentest
  requires_clis: httpx
  jobable: 'true'
  lifecycle: short
---

## Goal
Live HTTP discovery with **httpx**. Use under project plan / **project-manager** when present.

## Scripts
- scripts/run.py — project entry

## Run
```
sandbox_setup()
run_skill_script("http-prober", "scripts/run.py", command="httpx -l <targets> <flags>")
```

See [usage notes](references/USAGE.md) for paths, CLIs, and RoE.
