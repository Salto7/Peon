---
name: watchdog
description: Watchdog — Celery beat ticks + Unix socket stream. One-shot script per interval; no while-True loops. Use for continuous scheduled tick scripts.
allowed-tools: execute_code get_code_template run_command skill_view register_schedule pause_schedule resume_schedule
metadata:
  version: 1.3.0
  category: platform
  tags: platform
  jobable: 'true'
  manually_created: 'false'
  lifecycle: continuous
  max_iterations: '20'
---

## Goal
Run a **saved tick script** on a Celery interval. Live output goes to the UI via
Unix socket. Task is idle between ticks, running while a tick executes. Obey RoE
when under a project.

## Setup
1. `get_code_template("tick")` — one check per run (no `while True` / `time.sleep`)
2. `execute_code` once (runs first check + saves the script)
3. `register_schedule(interval_seconds=N, mode="script")`
4. `pause_schedule` / `resume_schedule` to control the beat

See [tick reference](references/TICK.md) for paths and streaming.
