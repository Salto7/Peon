---
name: analyzer
description: Post-stop synthesizer — aggregate the project plan, workspace evidence, and curated findings into a standalone project report. No new scanning. Use after project stop to synthesize findings/report.md.
allowed-tools: sandbox_setup run_skill_script list_objectives update_objective_status record_finding record_findings list_findings skills_list
metadata:
  version: 1.5.0
  category: platform
  tags: platform
  jobable: 'true'
  manually_created: 'false'
  lifecycle: long
---

## Goal
After the project stops, write a **standalone** `findings/report.md` from existing
evidence only. **Do not scan, exploit, or invent.**

Under the peon worker this skill is **host-synthesized** (structured Finding rows +
optional LLM, deterministic fallback). The `scripts/run.py` path remains for
offline / local runs.

## Order
Runs **last** on every project (planner bookend): `blueprint` → engagement skills →
**`analyzer`** (this skill) → `findings/report.md`.

## Sources (read-only)
1. Structured findings (DB / `workspace/findings_queue.jsonl` ingest)
2. Objective statuses + RoE
3. Curated `findings/*.md` and structured JSON handoffs (`corp-entities.json`, …)
4. Prefer curated artifacts; ignore numbered clones (`*.0001.md`)

See [report format](references/REPORT.md) for required sections and hard rules.
