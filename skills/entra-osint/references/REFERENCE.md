# entra-osint reference

## Prerequisites
- Requires a domain inventory from **`domain-enum`**
  (`workspace/domain-inventory.json` or an equivalent domain list).
- Block / wait if inventory is missing — do not invent domains or tenant IDs.
- `curl` is auto-provisioned from **`tools/catalog`** by the worker. See the Tools
  page if a binary is still missing after provision.

## OpenID technique
```
curl -s 'https://login.microsoftonline.com/<domain>/v2.0/.well-known/openid-configuration'
```

Response includes:
```json
{
  "issuer": "https://login.microsoftonline.com/<tenant-id>/v2.0"
}
```
Extract `<tenant-id>` from `issuer`. Never invent tenant IDs from model memory.

## Assessment paths
- Raw OpenID JSON → `workspace/raw/entra-osint/`
- Tenant map → `workspace/entra-tenants.json`
- Curated summary → `findings/entra-osint.md`

## Subcommands
| Subcommand | Purpose |
|------------|---------|
| `tenant` | Single domain → OpenID config → tenant ID |
| `from-domains` | File/stdin of domains → tenant map |
| `from-inventory` | `domain-enum` inventory JSON → tenant map + markdown |

Also:
```
run_skill_script("entra-osint", "scripts/run.py", command="from-domains workspace/domains.txt --out workspace/entra-tenants.json")
```

## Workflow
0. Confirm `workspace/domain-inventory.json` (or domain list) exists from `domain-enum`
1. `from-inventory …` (or `tenant` / `from-domains`)
2. Deduplicate domains that share the same tenant GUID
3. `record_finding` for confirmed discoveries (tenant IDs / related subjects with
   evidence) — not run/objective status
4. Promote `findings/entra-osint.md`

## RoE
- Public Microsoft endpoints only
- No password spraying, MFA bombing, or Graph enumeration requiring auth
- Only probe domains from the project inventory / in-scope seeds
