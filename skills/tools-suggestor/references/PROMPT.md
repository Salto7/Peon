# tools-suggestor

Write a `tools/catalog/*.yaml` entry so the tool installs and passes checks in the Peon sandbox image (`peon-sandbox:local`, Kali rolling). Many CLIs are already on `PATH`. A recipe is still required so a missing binary can be installed the same way in a project sandbox.

Return **JSON only** (no markdown wrapper):

```json
{
  "id": "kebab-id",
  "yaml": "full catalog YAML as one string",
  "install_script": "bash for {id}.sh, or \"\"",
  "notes": "one short paragraph: why this recipe"
}
```

## Operator-supplied install steps come first

If the operator's request already states the exact commands that install the
tool (a README snippet, a `git clone` + build sequence, etc.), that is ground
truth — reuse those commands verbatim as a `type: custom` step (see below)
instead of substituting a different method you are guessing at (do not swap
in a `github_release`/`apt` guess just because it is simpler). Adapt only
what non-interactive, root-as-default execution requires: drop `sudo` (the
script already runs as root), and add any `apt-get install -y
--no-install-recommends …` line the operator's steps assumed was already
done (a compiler, `git`, a language runtime). Only fall back to the general
preference order below when the operator did not say how to install the
tool.

## Choosing an install method

Otherwise prefer the easiest option that works on Kali (same order as catalog provision):

- **Already a Kali/Debian package?** → `type: apt` only (example: `sqlmap` → package `sqlmap`, binary `sqlmap`).
- **Release binary on GitHub?** → `type: github_release`.
- **Clone a repo and run a file inside it (a script, not a compiled build)?** → `type: apt` for git and the interpreter, then `type: git_clone` with `repo`, `entrypoint`, and `binary`.
- **PyPI?** → `type: pip` (apt the interpreter first if verify needs it).
- **Builds from source (`make`, `go install`, `cargo build`, …), or nothing above fits** → `type: custom` (inline command or `{id}.sh`). Put `install_script` in JSON only when YAML says `command: {id}.sh`.

## Install step field reference (exact keys — do not invent others)

- `apt` / `pip`: `packages` — a list of package names. No other keys.
- `github_release`: `repo` (`owner/name`), `binary` (name installed onto `PATH`).
  Optional: `asset_substr` (lowercase substring matched against release asset
  filenames; defaults to `linux_amd64` — only set it when the real asset name
  needs a different needle, e.g. `linux-amd64` or the tool's own name), `tag`
  (a specific release tag; omit for latest). There is no `asset`,
  `asset_pattern`, or `version` key — those are silently ignored.
- `git_clone`: `repo` (`owner/name`), `entrypoint` (repo-relative path to the
  script that becomes the binary — not a build artifact), `binary`. Optional:
  `ref`, `depth`. This step does not run `make`/`go build`/any build command;
  use `custom` for that.
- `custom`: `command` — either one inline shell line, or the literal
  `{id}.sh` to pull the script from `install_script` instead. Optional:
  `timeout` (seconds, default 600).

## Custom scripts

If you use `custom`, the script runs **before** any other install steps. It cannot rely on a later `apt` line to install prerequisites (e.g. `git` or an interpreter). Either run `apt-get install …` inside the script, or don’t use `custom` for that tool.

If the script compiles C/C++ (`make`, `cc`, `gcc`) or runs a toolchain that
needs cgo (`go install` on most modules), `apt-get install` `build-essential`
— not bare `gcc`. On Kali/Debian, `gcc` alone does not pull in `libc6-dev`,
so system headers (`stdint.h`, `pthread.h`, `grp.h`, …) are missing and the
compile fails; `build-essential` pulls in `libc6-dev`, `gcc`, `make`, and
`dpkg-dev` together.

If the script runs `git clone`, clone into a `mktemp -d` workdir with a
`trap 'rm -rf "$workdir"' EXIT`, the same way the catalog's own
`git_clone`/`github_release` steps do — never clone into the current
directory. The script's cwd is a shared, persistent lab workspace, not a
throwaway container; a bare `git clone` there leaves the checkout behind for
every run after it, and a second attempt to install the same tool fails with
"destination already exists".

Other step types run only if install still fails verification after `custom`.

## YAML checklist

- Required fields: `id`, `description`, `binary`, `install`, `verify`.
- `verify` is a **list** of steps, each with `command:` — not a single string.
- `binary` is the name on `PATH` after install; verify should use that same name.
- Install everything verify needs (interpreter, git, etc.) in your recipe.
- Use public, well-known sources only.
- Only use fields from the field reference above — an invented field name is
  silently ignored by the installer, not an error, so a wrong guess fails
  quietly at install time instead of at review time.
