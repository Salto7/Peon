# Tool Suggestor

Use when the operator wants to **add or install a CLI** in the Peon sandbox
(Learn / draft). Prefer the easiest install that works on Debian bookworm.

## Output contract
JSON only:

```json
{
  "id": "kebab-id",
  "yaml": "full catalog YAML as one string",
  "install_script": "bash for {id}.sh, or \"\"",
  "notes": "one short paragraph: why this recipe"
}
```

## Install priority
1. `apt` — if in Debian repos
2. `github_release` — release binaries
3. `git_clone` — with apt deps for the interpreter first
4. `pip`
5. `custom` — last resort (`install_script` only when YAML says `command: {id}.sh`)

## YAML checklist
- Required: `id`, `description`, `binary`, `install`, `verify`
- `verify` is a **list** of `{command: …}` steps
- `binary` must match what lands on PATH

## Hard rules
- Public, well-known sources only
- No engagement probes against RoE targets
