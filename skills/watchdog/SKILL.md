---
name: watchdog
description: Watchdog — periodic tick scripts in the Docker sandbox. One-shot script per interval; no while-True loops. Use for continuous scheduled checks.
allowed-tools: sandbox_setup sandbox_status run_cli run_periodic skill_view skills_list
metadata:
  version: 1.4.0
  category: platform
  tags: [platform, continuous]
  jobable: true
  manually_created: false
  lifecycle: continuous
  max_iterations: 20
---

## Goal
Run a **saved tick script** on a fixed interval inside the sandbox. Prefer one check
per tick (no `while True` / long `time.sleep` inside the script). Obey RoE when under
a project.

## Setup
1. Write a tick script under `/workspace/cache/tick.py` with `run_cli`.
2. Smoke it once: `run_cli("python3 /workspace/cache/tick.py")`.
3. Schedule repeats with `run_periodic(command=…, interval_seconds=N, duration_seconds=M)`.

See [tick reference](references/TICK.md) for paths and streaming notes.
