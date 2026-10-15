---
name: http-prober
description: httpx HTTP probing in the sandbox (web/recon). In-scope URLs only. Use when probing live HTTP services with httpx.
allowed-tools: Bash Read
compatibility: Requires httpx
---

## Goal
Live HTTP discovery with **httpx**. Prefer running as a **skill worker under
`recon-specialist` or `web-crawler-expert`** via `spawn_work(..., skill_ids="http-prober")`.
Solo under a web-primary objective is fine.

If a scanner woke you with a batch of web ports, `check_inbox` first and probe
those targets before anything else; as results land, `send_message` them back to
the sender and your parent. If you expect more targets from an upstream scan,
`go_idle` between batches instead of exiting.

## Scripts
- scripts/run.sh — entry (argv)

## Run
```
run_skill_script("http-prober", "scripts/run.sh", args='["-u", "https://acme.com", "-silent"]')
```

## Collaboration & memory
- Before repeating a method, tool, or target, `recall_memory`; `save_memory` for durable lessons a later agent would otherwise rediscover.
- Report up: `send_message` findings, blockers, and scope changes to your parent / role agent — blockers and scope/RoE changes reach the governance PM.
- Stream, don't hoard: as you discover something a waiting agent needs, `list_team` then `send_message` it (OBJ-n or role id) so it can start while you continue — batch per subject, do not wait for completion.
- `check_inbox` between steps; when you can only wait on another agent's output, `go_idle` (never poll).
