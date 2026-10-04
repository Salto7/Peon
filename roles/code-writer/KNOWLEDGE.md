# Code Writer (temporary)

Placeholder for a fuller coding role. **Today** it mirrors the old `skill-writer`
skill: scaffold a lint-clean skill under `workspace/learn/skill/<name>/`.

## Expected layout
- `SKILL.md` — agentskills frontmatter + Peon metadata
- `scripts/run.py` — thin entry
- optional `references/*.md` when linked from the body

## Prefer JSON when authoring via Learn
```json
{
  "name": "kebab-name",
  "skill_md": "full SKILL.md…",
  "files": {"scripts/run.py": "…"},
  "notes": "short rationale",
  "suggested_tools": ["catalog-tool-id"]
}
```

## Hard rules
- Directory name == frontmatter `name`
- Do not invent Cursor/Claude tool names in `allowed-tools`
- Prefer catalog CLIs + `provision_cli` over custom installs
- No engagement probes
