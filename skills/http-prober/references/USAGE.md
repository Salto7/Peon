# http-prober usage

## CLIs
`httpx` is auto-provisioned from **`tools/catalog`** by the worker. See the Tools page
if a binary is still missing after provision.

## Paths
- Target lists / raw JSON → `workspace/`
- Curated tables → `findings/http-prober.md` (not `findings/report.md`)
- Paths must stay under this project/run workspace

## Rules
- Obey RoE — in-scope hosts/URLs only
- `record_finding` for notable live services
- Reuse tool output; never invent probe results
