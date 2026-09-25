# project-manager execution reference

## Paths
- Scratch → `workspace/`
- Phase outputs → `findings/<skill-or-phase>.md` (+ structured JSON under `workspace/`)
- Final `findings/report.md` → **analyzer** (Stop) or an explicit reporting objective

## Sequencing & subgraphs
- Honor each skill’s prerequisites and project-plan `depends_on`
- Heavy pipelines: `run_skill_script` in **this** job; independent later phases:
  one `spawn_subagent` each → `wait_for_subagents` (pass required `skill_names`; never
  tell a child to skip upstream work)
- One worker per phase unless the operator asks for corroboration
- Hand off via `workspace/` JSON the next skill documents — not by parsing `findings/*.md`
- On failure: re-run the skill workflow; don’t shrink inventories to seed-only

## Rules
1. CLIs from skill `requires_clis` are auto-provisioned from **`tools/catalog`**
   by the worker. Use the Tools page for catalog entries — never shell `apt-get`
   inside `run_skill_script` `command=`.
2. Prefer dedicated skill scripts over ad-hoc install commands.
3. Never invent subjects or results when a skill or tool exists.
4. For investigative work, split the objective into evidence questions, check the catalog
   for a dedicated skill first, and use independent subagents only for genuinely parallel
   evidence gathering.
5. `record_finding` for engagement discoveries about subjects (any asset class) with
   evidence — free-form `kind` / `asset_type`; not job/objective/agent status. One row
   per discovery.
6. Continuous monitoring: use the **`watchdog`** skill (`run_periodic` + tick script),
   not ad-hoc `while True` loops in this job.
7. Promote reusable scripts to `findings/code/`; avoid mid-run `skill_manage` when skill
   learning is enabled.
