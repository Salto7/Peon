---
name: skill-writer
description: Draft a Claude Agent Skill (SKILL.md + a normal script) from an operator prompt. The pack must pass skills-ref so it can run in Claude and in Peon. Use when creating or scaffolding a new skill.
allowed-tools: Bash Read
---

## Goal
Produce a **skills-ref-clean** Claude Agent Skill under `workspace/learn/skill/<name>/`:

- SKILL.md — Agent Skills frontmatter (name, description, allowed-tools Bash and Read)
- a bash entry with a shebang that execs the required CLI
- optional reference docs when the body links them

Do not emit Peon-only helpers or `metadata.jobable`.

## Scripts
- `scripts/run.sh` — authoring entry (argv = skill request)

## Lint requirements (must pass)
- Frontmatter name + description; directory name == name
- `allowed-tools` uses Claude names (`Bash`, `Read`, `Write`, `Edit`)
- Scripts are normal executables with a shebang
- Body file refs must exist

See [prompt](references/PROMPT.md) for the JSON contract.

## Run
```
run_skill_script("skill-writer", "scripts/run.sh", args='["Draft a skill that …"]')
```
