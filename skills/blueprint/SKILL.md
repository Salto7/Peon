---
name: blueprint
description: Blueprint mode — write/refine the project plan or plans/*.md only; no execution. Runs before project-manager. Use when writing or refining a plan without executing tools.
allowed-tools: sandbox_setup run_skill_script run_cli skills_list skill_view
metadata:
  version: 3.1.0
  category: platform
  tags: platform
  jobable: 'true'
  manually_created: 'true'
  lifecycle: short
  max_iterations: '8'
---

## Goal
Produce an actionable **blueprint** (phases, skills, paths, success criteria).
**Do not execute** — no installs, probes, or `spawn_subagent`.

## Order
Every project runs **`blueprint` first** and **`analyzer` last** (enforced by the
project planner). Middle objectives are engagement work.

1. **`blueprint`** (this skill) — confirm / refine `plans/latest.md`
2. Engagement skills (recon, OSINT, …)
3. **`analyzer`** — synthesize `findings/report.md`

## How to write the plan
Prefer the skill script (do not search the filesystem for skill paths):

```
run_skill_script("blueprint", "scripts/run.py", command="")
```

That writes/confirms `plans/latest.md` from job scope + brief. To supply a full
markdown plan, pass it as `command` (must start with `#` or include newlines):

```
run_skill_script("blueprint", "scripts/run.py", command="# Project Blueprint\n\n…")
```

Use `run_cli` only for small in-place refinements of an existing `plans/latest.md`.

## Deliverables
- `plans/YYYY-MM-DD_HHMMSS-plan.md`
- `plans/latest.md` (always update)

See [planning notes](references/PLANNING.md) for project-aware content rules.
