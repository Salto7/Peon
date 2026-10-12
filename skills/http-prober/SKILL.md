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

## Scripts
- scripts/run.sh — entry (argv)

## Run
```
run_skill_script("http-prober", "scripts/run.sh", args='["-u", "https://acme.com", "-silent"]')
```
