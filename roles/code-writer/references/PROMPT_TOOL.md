# tools-suggestor

Write a `tools/catalog/*.yaml` entry so the tool installs and passes checks in a Docker lab based on **debian:bookworm-slim**. That image is nearly empty: no `git`, no `python3`, no extra CLIs until your recipe adds them.

Return **JSON only** (no markdown wrapper):

```json
{
  "id": "kebab-id",
  "yaml": "full catalog YAML as one string",
  "install_script": "bash for {id}.sh, or \"\"",
  "notes": "one short paragraph: why this recipe"
}
```

## Choosing an install method

Use the **easiest** option that works on Debian bookworm (same order as runtime
`provision_cli` / InstallResolver):

- **In Debian repos?** → `type: apt` only (example: `sqlmap` → package `sqlmap`, binary `sqlmap`).
- **Release binary on GitHub?** → `type: github_release`.
- **Clone a repo and run a file inside it?** → `type: git_clone` with `repo`, `entrypoint`, `binary`, plus `type: apt` for the runtime interpreter/deps the entrypoint expects (it might be `python3`, `nodejs`, etc.). List apt steps **before** git_clone so the clone has its interpreter.
- **PyPI?** → `type: pip` (install the interpreter runtime via apt if verify uses it).
- **Only when nothing above fits** → `type: custom` (inline command or `{id}.sh`). Put `install_script` in JSON only when YAML says `command: {id}.sh`.

## Custom scripts

If you use `custom`, the script runs **before** any other install steps. It cannot rely on a later `apt` line to install prerequisites (e.g. `git` or an interpreter). Either run `apt-get install …` inside the script, or don’t use `custom` for that tool.

Other step types run only if install still fails verification after `custom`.

## YAML checklist

- Required fields: `id`, `description`, `binary`, `install`, `verify`.
- `verify` is a **list** of steps, each with `command:` — not a single string.
- `binary` is the name on `PATH` after install; verify should use that same name.
- Install everything verify needs (interpreter, git, etc.) in your recipe.
- Use public, well-known sources only.
