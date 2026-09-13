# mcp-bridge reference

## Paths
- Server cwd / `.env` / venvs → job `mcp/<name>/` under workspace
- Curated glue → `findings/code/`
- Do not write `findings/report.md` (analyzer)

## Secrets
Install runtimes in the **sandbox** before `mcp_ensure`. Secrets via `.env` /
`env_from_secrets` — never paste tokens into skills or scripts/run.py.

## Spec (stdio)
```json
{"name":"myserver","transport":"stdio","command":"uv","args":["run","main.py"],
 "cwd":"optional/under/job","env_file":"optional.env","env_from_secrets":["API_TOKEN"]}
```

Summarize from **real** tool results only.
