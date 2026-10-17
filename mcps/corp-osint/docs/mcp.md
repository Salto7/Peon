# corp-OSINT as an MCP server

corp-OSINT exposes its pipeline and plugins over the [Model Context Protocol](https://modelcontextprotocol.io/) using stdio. The MCP host supplies the reasoning; corp-OSINT performs the public-source research.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) on `PATH`
- The repository checked out locally (or use the `uvx --from git+…` form below)

```bash
cd /path/to/corp-OSINT
uv sync
uv run playwright install chromium   # once — needed for Playwright stages
```

No API key is required for the core flow. Optional env vars: `URLSCAN_API_KEY`,
`DNSLYTICS_API_KEY`, `SEC_USER_AGENT`, `CORP_OSINT_PROXY`.

## Register with any MCP host

corp-OSINT speaks plain MCP over **stdio**, so any MCP-capable client or agent
can use it. The server is one command:

```text
uv --directory /absolute/path/to/corp-osint run corp-osint-mcp
```

`--directory` makes it independent of the host's working directory. Hosts that
use the common `mcpServers` JSON format (Claude Code/Desktop, Cursor, Windsurf,
Cline, Zed-style configs, …) take:

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

The repo ships [`.mcp.json`](../.mcp.json) (relative `--project .`, so it works
when the host is started from the repo root). Other hosts: add the same
`command` + `args` through their own MCP settings, or CLI, for example:

```bash
claude mcp add corp-osint -- uv --directory /abs/path/corp-osint run corp-osint-mcp
codex  mcp add corp-osint -- uv --directory /abs/path/corp-osint run corp-osint-mcp
```

Without a checkout (installs from Git into an isolated env):

```bash
uvx --from git+https://github.com/Salto7/corp-osint corp-osint-mcp
# one-time browser download for Playwright stages:
uvx --from git+https://github.com/Salto7/corp-osint playwright install chromium
```

Custom agents using an MCP SDK can launch the same command with a stdio client.
No HTTP endpoint is provided. Pass environment variables (API keys, proxy) in
the host's `env` block for the server.

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
