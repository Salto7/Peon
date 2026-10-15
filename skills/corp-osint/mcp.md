# corp-OSINT as an MCP server

corp-OSINT exposes its existing pipeline and plugins over the [Model Context Protocol](https://modelcontextprotocol.io/) using stdio. The MCP host supplies the reasoning; corp-OSINT performs the public-source research.

## Install and verify

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

python -c "import mcp; print('mcp ok')"
```

The server has no API key of its own. Optional existing integrations are still configured through environment variables such as `URLSCAN_API_KEY`, `DNSLYTICS_API_KEY`, `SEC_USER_AGENT`, and `CORP_OSINT_PROXY`.

## Register with an MCP host

The repository includes a project-level `.mcp.json`:

```json
{
  "mcpServers": {
    "corp-osint": {
      "command": ".venv/bin/python",
      "args": ["mcp_server.py"]
    }
  }
}
```

Start Claude Code from this repository so the relative paths resolve correctly. Cursor can use the checked-in [`.cursor/mcp.json`](../.cursor/mcp.json) when this folder is opened as the workspace.

For Codex CLI, register the local stdio server explicitly with absolute paths:

```bash
codex mcp add corp-osint -- \
  /absolute/path/to/corp-OSINT/.venv/bin/python \
  /absolute/path/to/corp-OSINT/mcp_server.py
codex mcp list
```

For a global Claude Code registration, use absolute paths:

```bash
claude mcp add corp-osint -- \
  /absolute/path/to/corp-OSINT/.venv/bin/python \
  /absolute/path/to/corp-OSINT/mcp_server.py
```

Claude Desktop uses the same `command` plus `args` shape in its MCP configuration. Keep the server process local and use the project’s `.venv`; the server is stdio-only and does not require an HTTP endpoint.

## Available tools

- `set_target` — store company, primary domain, stock ticker, CIK, and optional proxy defaults for the process.
- `run_pipeline` — full staged pipeline. Slim arguments:
  - identity overrides: `company`, `domain`, `ticker`, `cik`
  - stage control: `skip` and/or `only` (arrays of stage names)
  - proxy: `proxy`, `direct`, `no_proxy`
- Individual plugins: `asn`, `azure_tenant`, `dangling_check`, `dnslytics_gtm`, `edgar`, `playwright_search`, `rdap`, `resolve_domains`, `tenant_domains`, `urlscan`.

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

Tuning caps (entity limits, verify max, etc.) use `PipelineConfig.limits` defaults and are not exposed on the MCP tool schema.

Typical host prompt:

```text
Call set_target for Microsoft, stock market ticker symbol MSFT, domain microsoft.com.
Then run_pipeline with skip=["playwright","dnslytics"], and summarize confirmed versus candidate domains.
```

Each call may override session defaults. Tool responses are JSON text so they work consistently across MCP hosts. Progress and rate-limit messages go to stderr (`progress [module] …`, `rate-limit …`); stdout is reserved for MCP JSON-RPC frames.

Use only for authorized, lawful public-source research. Respect each upstream service's terms, rate limits, and robots/access policies.
