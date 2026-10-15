---
name: analyzer
description: Post-stop synthesizer — aggregate the project plan, workspace evidence, and curated findings into a standalone project report. No new evidence collection. Use after project stop to synthesize findings/report.md.
allowed-tools: Bash Read
---

## Goal
After the project stops, write a **standalone** `findings/report.md` from existing
evidence only. **Do not collect new evidence or invent discoveries.**

## Order
Runs **last** on every project (planner bookend): `project-manager` → specialist
skills → **`analyzer`** (this skill) → `findings/report.md`.

## Sources (read-only)
1. Structured findings (`list_findings` / DB / `workspace/findings_queue.jsonl`)
2. Objective statuses + RoE
3. Workspace JSON handoffs under `workspace/` and `workspace/mcp/artifacts/`
   (pipeline reports, plugin dumps). Prefer the largest complete JSON for a
   given run over truncated previews.
4. Curated `findings/*.md` — supporting detail only

See [report format](references/REPORT.md) for required sections and hard rules.

## Required workflow
1. `list_workspace_artifacts` — note paths **and byte sizes**.
2. `list_findings` — board rows already curated by specialists.
3. For each substantial JSON artifact, `read_workspace_artifact` with
   `max_chars` ≥ file size (default tool limit is not enough for large MCP
   dumps). If the response says truncated, re-read higher.
4. From JSON arrays (`dangling_domains`, `domains`, entities, …): **enumerate
   every row** into the report; `record_findings` for security-relevant rows
   that are not already on the board.
5. Write `findings/report.md` (via `write_report_note` sections or a single
   full write) with exactly three top-level sections, in order: **Executive
   summary**; **Findings and observations** (with the two sub-sections
   "Steps taken and commands executed" and "Findings and their technical
   details"); **Recommendations**. Embed inventories and findings inline —
   never "see file X". See [report format](references/REPORT.md).

## Run
From this skill directory (offline / non-worker):

```
run_skill_script("analyzer", "scripts/run.sh", args='[]')
```
