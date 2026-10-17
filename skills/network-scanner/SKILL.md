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
3. Stream as you scan: when web ports (80/443/8080/8443/…) land, immediately
   `send_message` them to the live HTTP prober / `web` agent (`list_team` → its
   `OBJ-n` or role id) so it probes while this scan continues — batch per host,
   do not wait for the whole scan. `spawn_work` a prober only if none exists.
   Other services (LDAP/SMB/…) go the same way to `network-service-recon`.
   Do not bounce to the PM for work already in this expert's skill group.

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

## Collaboration & memory
- Before repeating a method, tool, or target, `recall_memory`; `save_memory` for durable lessons a later agent would otherwise rediscover.
- Report up: `send_message` findings, blockers, and scope changes to your parent / role agent — blockers and scope/RoE changes reach the governance PM.
- Stream, don't hoard: as you discover something a waiting agent needs, `list_team` then `send_message` it (to one agent, by its id) so it can start while you continue — batch per subject, do not wait for completion.
- `check_inbox` between steps; when you can only wait on another agent's output, `go_idle` (never poll).
