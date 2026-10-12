---
name: corp-osint
description: >
  Maps a company to subsidiaries, brands, websites, urlscan/Playwright/DNSLytics
  GTM discoveries, IdefixTools Entra tenant→domains, RDAP/WHOIS, ASN/IP ownership,
  Azure tenant IDs, and dangling parked domains still verified in Entra. Use for
  corporate OSINT, GTM/tenant reverse domains, or dangling DNS/Entra findings.
license: Apache-2.0
compatibility: >
  Python 3.10+, network, aiohttp, tenacity, Playwright chromium; optional whois,
  URLSCAN_API_KEY, DNSLYTICS_API_KEY. Cursor / Claude Code / Codex compatible.
metadata:
  author: corp-OSINT
  version: "3.0.0"
  category: osint
---

# Corporate OSINT — Entity Expansion (v3)

Passive public-source recon only. **Plugin architecture:**

- One entrypoint: `scripts/main.py` (or `python -m corp_osint`)
- One abstract base: `corp_osint.plugin.Plugin`
- All stages live in `corp_osint/plugins/` and inherit `Plugin`
- Orchestrator: `CorpOsintPipeline(Plugin)` runs registered plugins

## Install

From the **repo root** (`corp-OSINT/`), one-time:

```bash
cd /path/to/corp-OSINT

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
playwright install chromium

# optional (RDAP fallback / richer rate limits)
sudo apt install whois             # or: brew install whois
export SEC_USER_AGENT='CorpOSINT/3.0 (research; you@acme.com)'
export URLSCAN_API_KEY=...         # optional — higher urlscan limits
export DNSLYTICS_API_KEY=...       # optional — paid GTM reverse API
# optional proxy (HTTP or SOCKS) for aiohttp + Playwright:
export CORP_OSINT_PROXY='socks5://127.0.0.1:1080'
# export HTTP_PROXY=... HTTPS_PROXY=... ALL_PROXY=...   # also honored
# export NO_PROXY=localhost,127.0.0.1,.local
```

**Requirements:** Python 3.10+, network access. Re-activate the venv in every new shell:

```bash
source .venv/bin/activate
```

**Verify install:**

```bash
python skills/corp-osint/scripts/main.py --list-plugins
python skills/corp-osint/scripts/main.py --plugin azure_tenant --domain acme.com
```

Agents: run the Install block if `.venv` is missing or `main.py` fails with import / Playwright errors before running the pipeline.

## Agent rules (quality)

1. **Pipeline JSON is raw evidence, not truth.** Regex/EDGAR parsers can emit
   header junk (`Name of the Subsidiary`) or invent slug domains. Critically
   filter before presenting findings.
2. **Present the compact `summary` first** (also `summary_text`). Each domain row:
   `domain`, `sources`, `evidence`, `confidence`, `verified`.
3. **Do not invent subsidiary `.com`s.** Subsidiaries are names until urlscan,
   Entra tenant domains, DNSLytics, or a verified homepage links them.
4. Prefer AI judgment over brittle string matches when classifying parking pages,
   brand vs legal-shell names, and whether a domain belongs to the company.

## Quick run

```bash
source .venv/bin/activate
cd skills/corp-osint/scripts
python main.py "Acme Corp" --domain acme.com --out report.json
python main.py -v "Acme Corp" --domain acme.com --summary-only
python main.py --list-plugins
python main.py --plugin dnslytics_gtm --domain acme.com
```

Logging (stderr): default INFO stage progress; `-v` / `-vv` DEBUG; `-q` warnings only.
JSON stays on stdout. Reports are **compact by default** (no WHOIS dumps); `summary`
+ `summary_text` are always first keys. Use `--no-compact` for verbose blobs;
`--summary-only` for summary JSON alone.

Flags: `--skip-edgar`, `--skip-urlscan`, `--skip-playwright`, `--skip-dnslytics`,
`--skip-tenant-domains`, `--skip-dangling-check`, `--skip-asn`, `--asn-deep`,
`--max-enrich-domains N`, `--tenant-domains-max N`, `--summary-only`, `--no-compact`,
`--proxy URL`, `--direct`, `--no-proxy HOSTS`, `--plugin NAME`, `-v`/`-vv`, `-q`.

## Proxy (HTTP + SOCKS)

All aiohttp traffic and Playwright Chromium share one proxy setting:

```bash
# SOCKS5
python main.py "Acme Corp" --domain acme.com --proxy socks5://127.0.0.1:1080

# HTTP proxy + bypass list
python main.py --plugin azure_tenant --domain acme.com \
  --proxy http://127.0.0.1:8080 --no-proxy localhost,127.0.0.1

# Force direct (ignore env proxies)
python main.py --plugin azure_tenant --domain acme.com --direct
```

Also: `CORP_OSINT_PROXY`, `ALL_PROXY`, `HTTPS_PROXY`, `HTTP_PROXY`, and
`NO_PROXY` / `CORP_OSINT_NO_PROXY`. Requires `aiohttp-socks` (in `requirements.txt`).

## Architecture

| Piece | Role |
|-------|------|
| `scripts/main.py` | **Single entrypoint** |
| `corp_osint.plugin.Plugin` | **Abstract base** — every module inherits this |
| `corp_osint.plugins.*` | Concrete plugins (`edgar`, `urlscan`, …) |
| `corp_osint.pipeline.CorpOsintPipeline` | Orchestrator plugin (`name="pipeline"`) |
| `corp_osint.http.AsyncHttpClient` | aiohttp + throttle + 429 retries |

To add a stage: subclass `Plugin`, implement `async def run(**kwargs)`, register in
`plugins/__init__.py` → `PLUGIN_REGISTRY`.

## Checklist

```
- [ ] 1 Parent (EDGAR CIK / legal name / domain)
- [ ] 2 Subsidiaries / brands (names; filter Exhibit 21 headers)
- [ ] 3 Resolve parent domain only (no mass subsidiary .com guessing)
- [ ] 4 urlscan + Playwright domain expansion
- [ ] 5 DNSLytics: GTM tags → reverse domains
- [ ] 6 RDAP/WHOIS + ASN/IP + Azure tenants (parallel)
- [ ] 7 IdefixTools tenant lookup → more Entra-verified domains
- [ ] 8 Flag parked domains whose Entra tenant matches company (dangling)
- [ ] 9 Emit JSON + compact summary (company / domains / sources / confidence / verified)
- [ ] 10 Agent critically filters pipeline output before final answer
```

## Tenant domains (IdefixTools)

Same public flow as https://idefixtools.com/tenant-lookup.html:

```bash
python scripts/main.py --plugin tenant_domains --domain acme.com
python scripts/main.py --plugin tenant_domains --tenant-id <guid>
```

Uses `func-idefix-tenantlookup.azurewebsites.net/api/lookup|domains` plus
Autodiscover `GetFederationInformation` fallback.

## Entra OIDC verify (shared)

Once a company Entra tenant ID is known, every found domain is checked via shared
`corp_osint.entra.fetch_oidc` against:

`https://login.microsoftonline.com/{domain}/v2.0/.well-known/openid-configuration`

Matches (issuer tenant == company tenant) are marked `matches_company_tenant` /
`relationship_verified`. Reuses the same helper as `azure_tenant` and
`tenant_domains` (no duplicated OIDC parsing). Cap: `--entra-verify-max N`
(default 200). Skip with `--skip-entra-verify`.

## Dangling / parked / unregistered (Entra)

After tenant→domains + OIDC verify, `dangling_check` probes Entra-verified **apex**
domains only (skips subdomains like `app.acme.com` — RDAP/DNS risk is on the
registrable name). Flagged as dangling when Entra-verified **and** any of:

| kind | Meaning |
|------|---------|
| `parked` | Parking / for-sale page |
| `nxdomain` | DNS does not resolve |
| `unregistered` | RDAP not found / available |
| `unreachable` | DNS may work but HTTP dead |

Results: `dangling_domains[]` with `kind`, plus `dangling_check.counts`.
Cap with `--dangling-max-domains N` (default 200).

## DNSLytics / GTM

```bash
python scripts/main.py --plugin dnslytics_gtm --domain acme.com
# optional paid API:
export DNSLYTICS_API_KEY=...
```

Flow: Playwright homepage GTM extract → DNSLytics UI via Playwright → reverse
`html.tag:GTM-XXXX`. On HTTP 429: honor Retry-After / exponential backoff, recycle
browser page (up to 5 attempts), HTML scrape fallback. Spacing ~3.5s. Official
reverse API used when `DNSLYTICS_API_KEY` set.

## Retries / 429

- `AsyncHttpClient`: up to 6 attempts; parses `Retry-After`; per-host cooldown
- urlscan queries run **sequentially** (parallel free-tier calls 429 easily)
- Playwright: Bing retries + DuckDuckGo fallback; homepage link discovery;
  default `--playwright-max-queries 6`

## Speed notes

- Domain enrichments (RDAP/Azure/ASN) run concurrently
- Playwright preferred for discovery; urlscan still parallel where safe
- ASN defaults to **observed IPs only** (`--asn-deep` for prefixes)
- Caps: `--max-entities`, `--max-enrich-domains` (default 6)
- Host throttles in `corp_osint.config.ThrottleConfig`

## Compact summary shape

Report includes `summary` and `summary_text`:

```json
{
  "company": "Acme Corporation",
  "primary_domain": "acme.com",
  "domains": [
    {
      "domain": "acme.com",
      "sources": ["https://…"],
      "evidence": "Primary / query domain",
      "confidence": "high",
      "verified": true
    }
  ],
  "entities": [
    {
      "name": "Example Brand LLC",
      "domains": [],
      "sources": ["https://www.sec.gov/…"],
      "evidence": "Exhibit 21 …",
      "confidence": "high",
      "verified": true
    }
  ]
}
```

## Output rules

Cite sources; never invent CIKs/tenants/ASNs/GTM IDs or subsidiary domains;
separate confirmed vs candidate; no port scans.

## Agent paths

| Runner | Path |
|--------|------|
| Cursor | `.cursor/skills/corp-osint/` / `$corp-osint` |
| Claude Code | `.claude/skills/corp-osint/` / `/corp-osint` |
| Subagent | `.cursor/agents/corp-osint.md` |

See [references/sources.md](references/sources.md) and [references/report-schema.md](references/report-schema.md).
