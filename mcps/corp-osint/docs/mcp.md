# corp-OSINT as an MCP server

corp-OSINT exposes its pipeline and plugins over the [Model Context Protocol](https://modelcontextprotocol.io/) using stdio. The MCP host supplies the reasoning; corp-OSINT performs the public-source research.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) on `PATH`
- Open / run from the **repository root** (so `--project .` resolves)

```bash
cd /path/to/corp-OSINT
uv sync
uv run playwright install chromium   # once — needed for Playwright stages
```

No API key is required for the core flow. Optional env vars: `URLSCAN_API_KEY`,
`DNSLYTICS_API_KEY`, `SEC_USER_AGENT`, `CORP_OSINT_PROXY`.

## Register with an MCP host

Project [`.mcp.json`](../.mcp.json) (and [`.cursor/mcp.json`](../.cursor/mcp.json)):

```json
{
  "mcpServers": {
    "corp-osint": {
      "command": "uv",
      "args": ["run", "--project", ".", "python", "mcp_server.py"]
    }
  }
}
```

Start Claude Code or Cursor from this repository so relative `--project .` works.

For Codex CLI (absolute paths):

```bash
codex mcp add corp-osint -- \
  uv run --project /absolute/path/to/corp-OSINT python /absolute/path/to/corp-OSINT/mcp_server.py
```

Or equivalent with `uv` as command and args split per your host’s schema:

```bash
codex mcp add corp-osint -- uv -- \
  run --project /absolute/path/to/corp-OSINT python mcp_server.py
```

Global Claude Code registration:

```bash
claude mcp add corp-osint -- \
  uv run --project /absolute/path/to/corp-OSINT python mcp_server.py
```

Claude Desktop uses the same `command` + `args` shape. stdio only — no HTTP endpoint.

## Available tools

- `set_target` — company, domain, ticker, CIK, optional proxy defaults
- `run_pipeline` — full pipeline; optional `skip` / `only` stage arrays; proxy overrides
- Plugins: `asn`, `azure_tenant`, `dangling_check`, `dnslytics_gtm`, `edgar`,
  `playwright_search`, `rdap`, `resolve_domains`, `tenant_domains`, `urlscan`

### Stage names (`skip` / `only`)

| Stage | Meaning |
|-------|---------|
| `edgar` | SEC EDGAR subsidiaries |
| `urlscan` | urlscan.io domain expansion |
| `playwright` | Playwright SERP / homepage discovery |
| `asn` | ASN / IP ownership |
| `dnslytics` | GTM reverse via DNSLytics |
| `tenant_domains` | IdefixTools Entra tenant → domains |
| `entra_verify` | OIDC verify domains against company tenant |
| `dangling` | Parked / NXDOMAIN / unregistered Entra domains |
| `resolve_sites` | Resolve parent website candidates |

Tuning caps live in `PipelineConfig.limits` defaults (not on the slim MCP schema).

Typical host prompt:

```text
Call set_target for Microsoft, stock market ticker symbol MSFT, domain microsoft.com.
Then run_pipeline with skip=["playwright","dnslytics"], and summarize confirmed versus candidate domains.
```

Progress and rate-limit messages go to stderr; stdout is reserved for MCP JSON-RPC.

Use only for authorized, lawful public-source research.
