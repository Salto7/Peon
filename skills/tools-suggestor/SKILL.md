---
name: tools-suggestor
description: >
  Suggest a tools/catalog YAML (and optional {id}.sh) for installing a CLI in the
  Peon sandbox. Use when an operator describes a tool to add or asks how to install it.
allowed-tools: run_skill_script skills_list skill_view
metadata:
  version: 1.0.0
  category: platform
  tags: catalog,tools,authoring
  jobable: 'false'
  lifecycle: short
  max_iterations: '6'
---

## Goal
Turn a natural-language tool request into a **valid catalog YAML** that matches
`tools/CATALOG.md` install priorities
(`custom` → `apt` → `github_release` → `pip` / `git_clone`).

## Scripts
- `scripts/run.py` — project entry (writes suggestion under `workspace/learn/`)

## Run
```
run_skill_script("tools-suggestor", "scripts/run.py", command="<tool request>")
```

## Output
- `workspace/learn/tool.yaml` — suggested catalog entry
- `workspace/learn/tool.sh` — optional custom install script (only when needed)
- `workspace/learn/notes.md` — short rationale

See [prompt](references/PROMPT.md) for the JSON contract the authoring path uses.
