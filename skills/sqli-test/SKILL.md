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
