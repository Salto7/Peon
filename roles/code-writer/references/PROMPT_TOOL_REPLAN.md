# code-writer tool replan

Fix a failed tools/catalog install recipe for debian:bookworm-slim.

Return **JSON only**:

```json
{
  "id": "kebab-id",
  "yaml": "full catalog YAML as one string",
  "install_script": "bash for {id}.sh, or \"\"",
  "notes": "what changed and why"
}
```

Read REQUEST.md for the operator prompt, current YAML/script, and the install error.
Prefer the easiest install method that will verify on bookworm-slim.
