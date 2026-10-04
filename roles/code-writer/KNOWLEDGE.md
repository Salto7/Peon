# Code Writer (OpenCode)

Learn Toolsmith binds to this role via `LEARN_AUTHORING_ROLE` / `authoring.engine`.
Swap prompts or `authoring.engine` here — do not hardcode authoring in Peon.

## Environment
- LiteLLM proxy must be enabled (`LLM_PROXY_ENABLED`) — OpenCode talks to `/v1`.
- Prefer the Learn lab staging mount; write drafts under `./out/`.
- Bootstrap: `assets/bootstrap_opencode.sh` (declared in ROLE.yaml `authoring.bootstrap`).

## Role pack layout (`roles/<id>/`) — role-pack
- `ROLE.yaml` — metadata (id, goal, tools, hierarchy, …)
- `KNOWLEDGE.md` — instructions (loaded on activation)
- `assets/` — executables, templates, data (roles do **not** use `scripts/`)
- `references/` — on-demand docs / prompts

## Tool catalog layout
- YAML under `tools/catalog/` (see `tools/CATALOG.md`)
- optional `{id}.sh` for `type: custom`

## Hard rules
- Role directory name == `ROLE.yaml` `id` (kebab-case)
- Prefer catalog CLIs + `provision_cli` over custom installs
- No engagement probes; no inventing in-scope targets
- Engagement roles: keep `reasoning: false` unless they are managers
- Managers may set `advanced_reasoning: true`
