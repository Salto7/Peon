---
name: project-manager
description: Project manager — execute the project plan after blueprint; skills, subgraphs, engagement findings. Prefer run_skill_script / sandbox spine. Not analyzer. Use when executing a multi-objective project plan.
allowed-tools: sandbox_setup sandbox_status provision_cli run_skill_script run_cli spawn_subagent wait_for_subagents skills_list skill_view list_objectives update_objective_status record_finding record_findings list_findings
metadata:
  version: 2.4.0
  category: platform
  aliases: assessment-manager
  jobable: 'true'
  manually_created: 'true'
  lifecycle: long
  max_iterations: '40'
---

## Goal
Drive an **authorized project**: next ready project-plan objective → evidence →
`update_objective_status`. Obey RoE. Operator focus tags / skill picks are **starting
focus only** — discover more with `skills_list` / `skill_view` as needed.

## Order
1. **`blueprint`** — write/refine the project plan or `plans/latest.md` (no execution)
2. **`project-manager`** (this skill) — execute that plan
3. **`analyzer`** — after project Stop, standalone `findings/report.md`

Do not invent a plan from scratch here when the operator asked for planning only —
hand off to **`blueprint`** first.

## Execution (sandbox spine)
1. `sandbox_setup()` before installs or CLI runs.
2. Run skills with `run_skill_script`:
   - Dedicated: `run_skill_script("<skill>", "scripts/run.py", command="…")`
   - Ad-hoc shell: `run_cli("…")`

CLIs are provisioned from **tools/catalog** by the worker before skill runs.
