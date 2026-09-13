# Tools catalog (`tools/catalog`)

Sandbox CLIs are defined as YAML files under `tools/catalog/`.
Peon loads them via `ToolCatalog` / `CatalogProvisioner` before skill runs.

## File layout

```
tools/
  CATALOG.md                 # this doc
  catalog/
    _base.yaml               # image-tier stub (not provisioned per job)
    nmap.yaml
    dnsx.yaml
    dnsx.sh                  # optional custom install script (same stem as yaml)
    …
```

Rules:

- One tool per `*.yaml` (id usually matches the filename stem).
- Files starting with `_` are ignored as tools (`_base.yaml`).
- Optional custom install script: **at most one** `{id}.sh` beside `{id}.yaml`.

## Minimal YAML

```yaml
id: nmap
name: nmap
description: >
  Network scanner for authorized in-scope host discovery.
tier: catalog
binary: nmap
install:
  - type: apt
    packages: [nmap]
verify:
  - command: "nmap --version"
skills: [network-scanner]
tags: [recon]
```

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Stable catalog key (kebab/lowercase). |
| `name` | no | Display name (defaults to `id`). |
| `description` | yes | What the tool does + RoE hints. |
| `tier` | no | `catalog` (default) or `image` (baked into image; skip provision). |
| `binary` | yes* | Primary CLI on `PATH` (*or use `binaries`). |
| `binaries` | no | Extra CLI names this entry provides. |
| `install` | yes* | List of install steps (*empty only for `tier: image`). |
| `verify` | yes | Shell checks that must succeed after install. |
| `skills` | no | Skill names that pull this tool in. |
| `tags` | no | Free-form labels for UI/search. |

## Install priority

Provision tries steps in this order and **stops when `verify` passes**:

1. **`custom`** — inline shell or `{id}.sh`
2. **`apt`** — Debian packages (batched when possible)
3. **`github_release`** — GitHub release asset → `/usr/local/bin`
4. **`pip`** / **`git_clone`** — remaining fallbacks

A failed `custom` step soft-fails into apt/github/… .

---

## Install options (examples)

### 1) `apt`

```yaml
install:
  - type: apt
    packages: [nmap, nmap-common]
```

### 2) `github_release`

```yaml
install:
  - type: github_release
    repo: projectdiscovery/dnsx          # or https://github.com/org/repo
    binary: dnsx                         # installed name under /usr/local/bin
    asset_substr: linux_amd64            # asset name hint
    # tag: v1.2.3                        # optional; default = latest
    source_urls:                         # optional RoE / allowlist hints
      - https://github.com/projectdiscovery/dnsx
```

### 3) `custom` — inline command

```yaml
install:
  - type: custom
    command: "curl -fsSL https://example.com/install.sh | bash"
    # timeout: 600
  - type: apt
    packages: [curl]                     # fallback if custom does not verify
```

### 4) `custom` — script beside the YAML

`tools/catalog/oddtool.yaml` + `tools/catalog/oddtool.sh`:

```yaml
# oddtool.yaml
install:
  - type: custom
    command: oddtool.sh                  # only {id}.sh is allowed
  - type: github_release
    repo: example/oddtool
    binary: oddtool
```

```bash
#!/usr/bin/env bash
# oddtool.sh — runs in the sandbox via bash
set -euo pipefail
# … install binary onto PATH …
```

Empty `command` on a `custom` step also means “use `{id}.sh` if present”.

### 5) `pip`

```yaml
install:
  - type: pip
    packages: [theHarvester]
```

### 6) `git_clone`

```yaml
install:
  - type: git_clone
    repo: dafthack/MFASweep               # owner/name
    entrypoint: MFASweep.ps1             # path inside the repo
    binary: mfasweep                     # symlink name on PATH
    # ref: main
    # depth: 1
```

### 7) Combined (recommended pattern)

```yaml
id: dnsx
name: dnsx
description: >
  Fast DNS toolkit (ProjectDiscovery). Authorized recon only.
tier: catalog
binary: dnsx
install:
  - type: custom
    command: dnsx.sh                     # optional fast path
  - type: github_release
    repo: projectdiscovery/dnsx
    binary: dnsx
    asset_substr: linux_amd64
  - type: apt
    packages: []                         # usually omit if unused
verify:
  - command: "dnsx -version"
    # must_match: "dnsx"                 # optional regex on stdout/stderr
    # must_not_match: "not found"
skills: [domain-enum]
tags: [recon, osint]
```

---

## Verify

```yaml
verify:
  - command: "tool --version"
    must_match: "tool"          # optional; case-insensitive regex
    must_not_match: "not found" # optional
```

All listed verify commands must exit `0` (and satisfy match rules) for the tool to count as installed.

## Aliases

Install step `type` aliases for `custom`: `command`, `shell`, `run`, `bash`, `script`.
Prefer **`custom`** in new YAML.

## Related code

- Loader / provisioner: `orchestrator/tools/catalog/catalog.py`
- Missing-CLI cascade (catalog → skill docs → planner): `orchestrator/runtime/resolve.py`
- Learn UI (suggest YAML / write skills): Peon **Learn** page + `tools-suggestor` / `skill-writer` skills
