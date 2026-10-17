# corp-osint

Lightweight proxy-aware OSINT tool that finds an organization's subsidiaries, brands and related from just a company name with an optional domain or Ticker. It also perform RDAP/WHOIS queries and finds ASN/IP. Then identifies Microsoft Entra tenant IDs and use it to verify domains correlation to the roganization. Finally it attempt to find dangling/parked domains that are still tied to that tenant 
from **public sources only**. Runs as a CLI or MCP server

No API keys required for the core flow (optional urlscan / DNSLytics keys raise limits). Proxy-aware (`--proxy`, `CORP_OSINT_PROXY`, standard `*_PROXY`; `--direct` /`NO_PROXY`). Per-host throttles and 429 backoff (incremental +200 ms -> 3 s waits).

> **Authorized use only. Do not run this tool against orgs without explicit written permission.



## Features

| Stage | What it does |
|-------|----------------|
| **EDGAR** | SEC filings → legal name, CIK, subsidiaries / brands |
| **Domain discovery** | urlscan + Playwright SERP / homepage expansion |
| **DNSLytics GTM** | GTM tags → reverse related domains |
| **RDAP / WHOIS** | Registration enrichment |
| **ASN / IP** | Observed IPs and ownership signals |
| **Entra** | Tenant lookup, tenant→domains, OIDC verify |
| **Dangling check** | Parked / NXDOMAIN / unregistered apex still in Entra |

## Install

```bash
git clone https://github.com/Salto7/corp-osint.git && cd corp-osint
# https://docs.astral.sh/uv/getting-started/installation/
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync && uv run playwright install chromium
# optional: whois CLI, SEC_USER_AGENT, URLSCAN_API_KEY, DNSLYTICS_API_KEY, CORP_OSINT_PROXY
```

Needs [uv](https://docs.astral.sh/uv/), Python 3.10+, network egress.

```bash
uv run python main.py --list-plugins
uv run python main.py --plugin azure_tenant --domain microsoft.com
```

## Quick start (CLI)

```bash
uv run python main.py "Example Corp" --domain example.com --out report.json
uv run python main.py "Example Corp" --domain example.com --ticker EXAM --summary-only
uv run python main.py "Example Corp" --domain example.com --skip playwright,urlscan
uv run python main.py --plugin edgar --company "Example Corp" --ticker EXAM
```

Stages for `--skip` / `--only`: `edgar`, `urlscan`, `playwright`, `asn`,
`dnslytics`, `tenant_domains`, `entra_verify`, `dangling`, `resolve_sites`.

Useful flags: `-v` / `-q`, `--proxy`, `--direct`, `--out`. Logs and JSON go via
the logger (stderr).

## MCP server

Same pipeline over [MCP](https://modelcontextprotocol.io/) stdio, usable from any
MCP-capable agent or client (not tied to one editor). Run
`uv sync && uv run playwright install chromium` once, then register this command
with your host:

```json
{
  "mcpServers": {
    "corp-osint": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/corp-osint", "run", "corp-osint-mcp"]
    }
  }
}
```

[`.mcp.json`](.mcp.json) is a ready-made project-local version. Flow:
`set_target` → `run_pipeline` (or a plugin tool). Progress on stderr; stdout is
MCP JSON-RPC. Setup for other hosts and `uvx`: [`docs/mcp.md`](docs/mcp.md).

## Docs & layout

| Doc | Contents |
|-----|----------|
| [`docs/SKILL.md`](docs/SKILL.md) | Agent playbook (portable skill; copy into your agent's skills dir) |
| [`docs/mcp.md`](docs/mcp.md) | MCP setup and tools |
| [`docs/sources.md`](docs/sources.md) | Source notes |
| [`docs/report-schema.md`](docs/report-schema.md) | Report JSON shape |

```text
corp-osint/
  pyproject.toml  main.py  mcp_server.py  .mcp.json
  corp_osint/     # library
  docs/
```

Env: `SEC_USER_AGENT`, `URLSCAN_API_KEY`, `DNSLYTICS_API_KEY`, `CORP_OSINT_PROXY`,
`HTTP(S)_PROXY` / `ALL_PROXY`, `NO_PROXY`.
