# code-writer OpenCode authoring contract

You author **Peon CrewAI roles** (and optionally companion catalog tools) using
OpenCode. Output must load cleanly via Peon's role loader (`ROLE.yaml`).

Reply by writing files under `./out/` and a `result.json` with:

```json
{
  "name": "kebab-role-name",
  "role_yaml": "full ROLE.yaml contents",
  "files": {
    "KNOWLEDGE.md": "markdown",
    "references/USAGE.md": "optional"
  },
  "notes": "short rationale",
  "suggested_tools": ["catalog-tool-id"]
}
```

## ROLE.yaml requirements

- `id` — lowercase kebab-case; matches directory name
- `label`, `crew_role`, `goal`, `backstory`
- `tools` — list from the live crew tool allowlist
  (`sandbox_status`, `provision_cli`, `run_cli`, `roe_status`, `assert_in_scope`,
  `record_finding`, `list_findings`, `list_objectives`, `update_objective_status`,
  `write_report_note`, …). Do not invent Cursor tool names.
- `hierarchy.reports_to` — usually `project-manager` for engagement specialists;
  empty for managers/authoring
- `mode` — `engagement` or `authoring`
- `reasoning` / `advanced_reasoning` — booleans
  - engagement specialists: `reasoning: false`, `advanced_reasoning: false`
  - project-manager: `reasoning: true`, `advanced_reasoning: true`
  - authoring roles: light `reasoning: true`, `advanced_reasoning: false`
- `requires_roe` — true for active network probing roles
- `tool_policy.allow_binaries` when the role runs CLIs

## OpenCode workflow

1. Ensure `opencode` is on PATH (install if needed).
2. Use the host-provided LiteLLM-compatible model endpoint.
3. Write `ROLE.yaml` + `KNOWLEDGE.md` under `./out/`.
4. Emit `result.json` with full file contents (not just paths).

## Role pack layout (role-pack)

```
roles/<id>/
├── ROLE.yaml       # required metadata
├── KNOWLEDGE.md    # instructions
├── assets/         # executables + templates (no scripts/)
└── references/     # optional docs
```

Do **not** create a `scripts/` directory on roles — put runnable helpers in `assets/`.

## Hard rules

- Never expand Rules of Engagement.
- Prefer catalog tools for installs; do not invent opaque custom installers.
- Do not scaffold legacy playbook packs — roles are the engagement catalog.
