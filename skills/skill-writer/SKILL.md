---
name: skill-writer
description: >
  Draft a Peon Agent Skill (SKILL.md + scripts/run.py) from an operator prompt,
  using available catalog tools. Output must pass SkillLinter. Use when creating
  or scaffolding a new engagement skill.
allowed-tools: run_skill_script skills_list skill_view
metadata:
  version: 1.0.0
  category: platform
  tags: skills,authoring,linter
  jobable: 'false'
  lifecycle: short
  max_iterations: '8'
---

## Goal
Produce a **lint-clean** skill directory layout under `workspace/learn/skill/<name>/`:

- SKILL.md — agentskills frontmatter + Peon metadata
- scripts/run.py — thin entry (prefer run_binary / skill_entry helpers)
- optional references docs when linked from the body

## Scripts
- `scripts/run.py` — project entry

## Run
```
run_skill_script("skill-writer", "scripts/run.py", command="<skill request>")
```

## Lint requirements (must pass)
- Frontmatter name + description; directory name == name
- Product fields under metadata (category, jobable, lifecycle, …)
- Body file refs must exist
- Prefer jobable true for engagement skills unless the operator says otherwise

See [prompt](references/PROMPT.md) for the JSON contract.
