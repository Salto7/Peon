---
name: analyzer
description: Post-stop synthesizer — aggregate the project plan, workspace evidence, and curated findings into a standalone project report. No new evidence collection. Use after project stop to synthesize findings/report.md.
allowed-tools: Bash Read
---

## Goal
After the project stops, write a **standalone** `findings/report.md` from existing
evidence only. **Do not collect new evidence or invent discoveries.**

Under the peon worker this skill is **host-synthesized** (structured Finding rows +
optional LLM, deterministic fallback). The `scripts/run.sh` path remains for
offline / local runs.

## Order
Runs **last** on every project (planner bookend): `project-manager` → specialist
skills → **`analyzer`** (this skill) → `findings/report.md`.

## Sources (read-only)
1. Structured findings (DB / `workspace/findings_queue.jsonl` ingest)
2. Objective statuses + RoE
3. Curated `findings/*.md` and structured JSON handoffs (`corp-entities.json`, …)
4. Prefer curated artifacts; ignore numbered clones (`*.0001.md`)

See [report format](references/REPORT.md) for required sections and hard rules.

## Run
From this skill directory:

```
run_skill_script("analyzer", "scripts/run.sh", args='[]')
```
