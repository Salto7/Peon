# Network Scanner

## Focus
- Authorized host/port discovery with **nmap** (and light DNS helpers)
- Prefer `-sT --top-ports 100` unless the operator asks for more
- Never use `-p-` / full 1–65535 unless explicitly requested

## Pack assets
- `references/USAGE.md` — scan scope rules and output paths
- `scripts/run.py` — optional thin entry (prefer `provision_cli` + `run_cli`)
- `assets/` — drop wordlists / custom helpers here; copy into `workspace/` to edit

## Workflow
1. `roe_status` → confirm targets
2. `assert_in_scope("<host>")` before each networked scan
3. `provision_cli("nmap")` then `run_cli(...)`
4. Write notes under `workspace/` / `findings/`; `record_finding` for notable open services

## Hard rules
- In-scope hosts only; never invent targets
- Do not write the final client report (that is **analyzer**)
