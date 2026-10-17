---
name: sqli-test
description: Authorized SQL injection testing on promoted in-scope targets only. Minimal impact; evidence-backed findings. Use when an objective calls for SQLi assessment.
allowed-tools: Bash Read
compatibility: Requires sqlmap
---

## Goal
Test for SQL injection **only** on in-scope, promoted targets. Verify RoE before
every probe. Prefer `run_skill_script` / `bash` with catalog binaries (`sqlmap`).

## Scripts
- `scripts/run.sh` — entry (argv)

## Run
```
run_skill_script("sqli-test", "scripts/run.sh", args='["-u", "<in-scope-url>", "--batch", "--level=1", "--risk=1"]')
```

## Collaboration & memory
- Before repeating a method, tool, or target, `recall_memory`; `save_memory` for durable lessons a later agent would otherwise rediscover.
- Report up: `send_message` findings, blockers, and scope changes to your parent / role agent — blockers and scope/RoE changes reach the governance PM.
- Stream, don't hoard: as you discover something a waiting agent needs, `list_team` then `send_message` it (to one agent, by its id) so it can start while you continue — batch per subject, do not wait for completion.
- `check_inbox` between steps; when you can only wait on another agent's output, `go_idle` (never poll).
