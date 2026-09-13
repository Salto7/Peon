# domain-enum reference

## Prerequisites / ordering
- For corporate recon, invoke the **`ai-osint-subsidiaries` `workflow` command**.
  It validates and checkpoints corporate discovery before invoking this engine.
- Do not manually chain discovery and this skill; that bypasses resumability and
  the failure gate.
- `requires_clis` are auto-provisioned from **`tools/catalog`** by the worker. If a
  binary is still missing, check the Tools catalog entry.

## Assessment paths
- Input handoff: `workspace/corp-entities.json`
- Domain seed summary: `workspace/corp-from-enum.json`
- Subdomains (line list): `workspace/subdomains.txt`
- Subdomains (JSON): `workspace/subdomains.json`
- Optional summary: `findings/domain-inventory.md`

## Optional targeted run
```
run_skill_script("domain-enum", "scripts/run.py", command="subdomains --from-corp --workspace workspace --out workspace/subdomains.txt --json-out workspace/subdomains.json")
```

## Subcommands
| Subcommand | Purpose |
|---|---|
| `from-corp` | Read names/domains from `workspace/corp-entities.json` |
| `pipeline` | Load corp/AI domains and run dnsx subdomain enumeration |
| `subdomains` | Run dnsx brute-force for supplied / corp domains |
| `extract` / `merge` / `rdap` | Supporting normalization and enrichment (`rdap` runs parallel workers) |

## dnsx notes
- Uses `dnsx -d <domains> -w <wordlist> -silent`.
- Default wordlist: assets/wordlists/subdomains.txt
- Override with `--wordlist /path/to/words.txt` or repeated `--words api --words admin ...`

## Async / parallel
- `reverse-whois` keywords concurrently (`--workers`, default `8`)
- `rdap` domain lookups concurrently (`--workers`, default `12`)
- `subdomains` is a single `dnsx` run; concurrency via dnsx `--threads`

## RoE
- Passive OSINT only
- Never claim ownership from subdomain presence alone
- Do not invent domains/subdomains from model memory
