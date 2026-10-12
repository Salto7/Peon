---
name: network-scanner
description: nmap in the project sandbox (recon). In-scope hosts only; prefer --top-ports unless all-ports is explicit. Use when scanning in-scope hosts/ports with nmap.
allowed-tools: Bash Read
compatibility: Requires nmap
---

## Goal
Authorized **nmap** scans under RoE. Scan the authorized scope **as given**
(full CIDR or host list) in this job — do not carve netblocks into smaller
`scope_slice` workers unless the operator explicitly asks.

## Workflow
1. `sandbox_status` / provision nmap as needed.
2. Run one (or a few) scan commands covering the authorized target set.
3. After open ports/services land, spawn in-lane follow-ons (`http-prober`,
   `web`, `network-service-recon`) via `spawn_work` when useful — do not bounce
   to the PM for work already in this expert's skill group.

## Output format (required)
- Write **greppable** output only: `-oG workspace/<label>.gnmap`
- Do **not** use `-oA`, `-oX`, or `-oN`
- Reuse the `.gnmap` for notes / `record_finding`

## Scripts
- scripts/run.sh — entry (argv)

## Run
```
run_skill_script("network-scanner", "scripts/run.sh", args='["-sT", "--top-ports", "100", "-oG", "workspace/scan.gnmap", "<target>"]')
```
