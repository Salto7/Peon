# network-scanner usage

## CLIs
`nmap` is auto-provisioned from **`tools/catalog`** by the worker. See the Tools page
if a binary is still missing after provision.

## Paths
- Raw output → `workspace/`
- Curated notes → `findings/network-scanner.md` (not `findings/report.md`)

## Rules
- Match requested port scope — never `-p-` / `1-65535` unless explicitly asked
- Obey RoE / `list_objectives`; `record_finding` for notable services
- Companion scanners: use a dedicated skill when one exists; ensure the CLI is in
  **`tools/catalog`** so the worker can provision it
- Reuse collected output for notes — do not re-scan just to fill text
