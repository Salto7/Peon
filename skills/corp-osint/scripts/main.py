#!/usr/bin/env python3
"""
corp-OSINT — single entrypoint.

Examples:
  python main.py "Acme Corp" --domain acme.com --out report.json
  python main.py "Acme Corp" --domain acme.com --summary-only
  python main.py --list-plugins
  python main.py --plugin edgar --company "Acme Corp" --ticker ACME
  python main.py --plugin dnslytics_gtm --domain acme.com
  python main.py --plugin tenant_domains --domain acme.com
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# Allow `python main.py` from scripts/ and `python -m corp_osint`
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from corp_osint.config import (  # noqa: E402
    HttpConfig,
    PipelineConfig,
    ThrottleConfig,
    resolve_proxy_url,
)
from corp_osint.http import AsyncHttpClient  # noqa: E402
from corp_osint.log import get_logger, setup_logging  # noqa: E402
from corp_osint.pipeline import CorpOsintPipeline  # noqa: E402
from corp_osint.plugins import PLUGIN_REGISTRY, create_plugin, list_plugins  # noqa: E402
from corp_osint.report import ensure_summary  # noqa: E402

log = get_logger("cli")


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="corp-osint",
        description="Corporate OSINT — plugin-based pipeline (single entrypoint)",
    )
    ap.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (-v DEBUG, -vv more HTTP detail)",
    )
    ap.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Quiet mode (warnings/errors only)",
    )
    ap.add_argument(
        "company",
        nargs="?",
        help="Company name (required for full pipeline)",
    )
    ap.add_argument("--cik")
    ap.add_argument("--ticker")
    ap.add_argument("--domain")
    ap.add_argument("--company", dest="company_opt", help="Alias for positional company")

    ap.add_argument("--list-plugins", action="store_true", help="List registered plugins")
    ap.add_argument(
        "--plugin",
        help="Run a single plugin by name (see --list-plugins). Default: full pipeline",
    )

    ap.add_argument("--skip-edgar", action="store_true")
    ap.add_argument("--skip-urlscan", action="store_true")
    ap.add_argument("--skip-playwright", action="store_true")
    ap.add_argument("--skip-asn", action="store_true")
    ap.add_argument("--skip-dnslytics", action="store_true")
    ap.add_argument("--skip-tenant-domains", action="store_true")
    ap.add_argument(
        "--skip-entra-verify",
        action="store_true",
        help="Skip OIDC-verify of found domains against company Entra tenant",
    )
    ap.add_argument(
        "--entra-verify-max",
        type=int,
        default=200,
        help="Max domains to OIDC-verify against company tenant",
    )
    ap.add_argument("--skip-dangling-check", action="store_true")
    ap.add_argument(
        "--dangling-max-domains",
        type=int,
        default=200,
        help="Max Entra domains to probe for parked/dead/unregistered",
    )
    ap.add_argument("--no-resolve-sites", action="store_true")
    ap.add_argument("--asn-deep", action="store_true")
    ap.add_argument("--max-entities", type=int, default=12)
    ap.add_argument("--max-enrich-domains", type=int, default=6)
    ap.add_argument("--tenant-domains-max", type=int, default=150)
    ap.add_argument("--tenant-id", help="Entra tenant GUID (tenant_domains plugin)")
    ap.add_argument("--playwright-engine", choices=("bing", "duckduckgo"), default="bing")
    ap.add_argument("--playwright-max-queries", type=int, default=6)
    ap.add_argument("--max-domains", type=int, default=5, help="dnslytics seed cap")
    ap.add_argument("--max-reverse", type=int, default=5, help="dnslytics GTM reverse cap")
    ap.add_argument("--out", help="Write JSON output path")
    ap.add_argument(
        "--summary-only",
        action="store_true",
        help="Print only compact summary JSON",
    )
    ap.add_argument(
        "--no-compact",
        action="store_true",
        help="Disable JSON size compaction (keep verbose WHOIS/probe blobs)",
    )
    ap.add_argument(
        "--proxy",
        metavar="URL",
        help=(
            "HTTP/HTTPS/SOCKS proxy (e.g. http://127.0.0.1:8080, "
            "socks5://127.0.0.1:1080). Default: CORP_OSINT_PROXY / *_PROXY env"
        ),
    )
    ap.add_argument(
        "--direct",
        action="store_true",
        help="Disable proxy (ignore CORP_OSINT_PROXY / HTTP(S)_PROXY / ALL_PROXY)",
    )
    ap.add_argument(
        "--no-proxy",
        dest="no_proxy_hosts",
        metavar="HOSTS",
        help="Comma-separated hosts that bypass the proxy (also NO_PROXY env)",
    )
    return ap


def _company(args: argparse.Namespace) -> str | None:
    return args.company_opt or args.company


def _http_config(args: argparse.Namespace) -> HttpConfig:
    http = HttpConfig()
    if args.direct:
        http.proxy = None
    elif args.proxy is not None:
        http.proxy = resolve_proxy_url(args.proxy)
    if args.no_proxy_hosts is not None:
        http.no_proxy = tuple(
            p.strip() for p in args.no_proxy_hosts.replace(";", ",").split(",") if p.strip()
        )
    return http


def _pipeline_config(args: argparse.Namespace) -> PipelineConfig:
    company = _company(args)
    if not company:
        raise SystemExit("company name required for full pipeline")
    return PipelineConfig(
        company=company,
        cik=args.cik,
        ticker=args.ticker,
        domain=args.domain,
        max_entities=args.max_entities,
        max_enrich_domains=args.max_enrich_domains,
        tenant_domains_max=args.tenant_domains_max,
        resolve_sites=not args.no_resolve_sites,
        use_edgar=not args.skip_edgar,
        use_urlscan=not args.skip_urlscan,
        use_playwright=not args.skip_playwright,
        use_asn=not args.skip_asn,
        use_dnslytics=not args.skip_dnslytics,
        use_tenant_domains=not args.skip_tenant_domains,
        entra_verify_domains=not args.skip_entra_verify,
        entra_verify_max=args.entra_verify_max,
        check_dangling_parked=not args.skip_dangling_check,
        dangling_max_domains=args.dangling_max_domains,
        asn_deep=args.asn_deep,
        playwright_engine=args.playwright_engine,
        playwright_max_queries=args.playwright_max_queries,
        dnslytics_max_domains=args.max_domains,
        dnslytics_max_gtm_reverse=args.max_reverse,
        compact_report=not args.no_compact,
        http=_http_config(args),
    )


async def _run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    cfg = _pipeline_config(args)
    log.info("running full pipeline")
    return await CorpOsintPipeline(cfg).run()


async def _run_plugin(args: argparse.Namespace) -> dict[str, Any]:
    name = args.plugin
    if name == CorpOsintPipeline.name:
        return await _run_pipeline(args)
    if name not in PLUGIN_REGISTRY:
        known = ", ".join(sorted(PLUGIN_REGISTRY) + [CorpOsintPipeline.name])
        raise SystemExit(f"Unknown plugin {name!r}. Known: {known}")

    cfg = None
    company = _company(args)
    if company:
        cfg = _pipeline_config(args)

    log.info("running plugin=%s company=%s domain=%s", name, company, args.domain)
    async with AsyncHttpClient(
        cfg.http if cfg else _http_config(args),
        cfg.throttle if cfg else ThrottleConfig(),
    ) as http:
        plugin = create_plugin(name, http, cfg)
        try:
            kwargs: dict[str, Any] = {}
            if company:
                kwargs["company"] = company
            if args.domain:
                kwargs["domain"] = args.domain
            if args.cik:
                kwargs["cik"] = args.cik
            if args.ticker:
                kwargs["ticker"] = args.ticker
            if args.tenant_id:
                kwargs["tenant_id"] = args.tenant_id
            if name == "dnslytics_gtm":
                kwargs["max_domains"] = args.max_domains
                kwargs["max_gtm_reverse"] = args.max_reverse
            if name == "tenant_domains":
                kwargs["max_domains"] = args.tenant_domains_max
            if name == "playwright_search":
                kwargs["engine"] = args.playwright_engine
                kwargs["max_queries"] = args.playwright_max_queries
            if name == "resolve_domains":
                if not company and not args.domain:
                    raise SystemExit("resolve_domains needs --company and/or --domain")
                kwargs["name"] = company or args.domain
                kwargs["parent_domain"] = args.domain
            if name == "asn":
                kwargs["deep"] = args.asn_deep
            if name == "dangling_check":
                # Probe listed domains; treat --domain as Entra-verified seed when --tenant-id set
                seeds = []
                if args.domain:
                    seeds = [args.domain]
                kwargs["domains"] = seeds
                kwargs["entra_verified_domains"] = seeds if args.tenant_id else []
                kwargs["tenant_id"] = args.tenant_id
                kwargs["max_domains"] = args.dangling_max_domains
            if name == "azure_tenant" and args.tenant_id and args.domain:
                kwargs["expected_tenant_id"] = args.tenant_id
                kwargs["domains"] = [args.domain]
            log.debug("plugin kwargs keys=%s", sorted(kwargs))
            raw = await plugin.run(**kwargs)
            # Wrap single-plugin output so summary is always present
            if isinstance(raw, dict) and "summary" in raw and "query" in raw:
                return ensure_summary(raw)
            wrapped = {
                "query": company or args.domain or name,
                "plugin": name,
                "parent": {
                    "name": company,
                    "primary_domain": args.domain,
                },
                "result": raw,
                "domains": [],
                "entities": [],
                "dangling_domains": [],
            }
            # Promote common plugin shapes into summary-friendly fields
            if isinstance(raw, dict):
                if raw.get("domain") and raw.get("azure_tenant"):
                    wrapped["domains"] = [
                        {
                            "domain": raw["domain"],
                            "discovery": {
                                "relationship_verified": True,
                                "confidence": "high",
                                "evidence": "plugin azure_tenant",
                                "sources": [],
                            },
                            "azure_tenant": raw.get("azure_tenant"),
                        }
                    ]
                    wrapped["parent"]["entra_tenant_id"] = (raw.get("azure_tenant") or {}).get(
                        "tenant_id"
                    )
                if isinstance(raw.get("domains"), list) and name == "tenant_domains":
                    wrapped["tenant_domains"] = raw
                    wrapped["parent"]["entra_tenant_id"] = raw.get("tenant_id")
                if isinstance(raw.get("dangling_domains"), list):
                    wrapped["dangling_domains"] = raw["dangling_domains"]
            return ensure_summary(wrapped)
        finally:
            await plugin.close()


def main(argv: list[str] | None = None) -> None:
    ap = _build_parser()
    args = ap.parse_args(argv)
    setup_logging(verbosity=args.verbose, quiet=args.quiet)

    if args.list_plugins:
        rows = list_plugins() + [
            {
                "name": CorpOsintPipeline.name,
                "description": CorpOsintPipeline.description,
            }
        ]
        for row in sorted(rows, key=lambda r: r["name"]):
            raw = (row.get("description") or "").strip()
            desc = raw.splitlines()[0][:80] if raw else ""
            print(f"{row['name']:20}  {desc}")
        return

    try:
        if args.plugin:
            result = asyncio.run(_run_plugin(args))
        else:
            result = asyncio.run(_run_pipeline(args))
    except KeyboardInterrupt:
        log.error("interrupted")
        raise SystemExit(130) from None
    except Exception as e:  # noqa: BLE001
        log.exception("fatal: %s", e)
        raise SystemExit(1) from e

    if not isinstance(result, dict):
        result = {"result": result}
    result = ensure_summary(result)

    summary = result.get("summary")
    summary_text = result.get("summary_text")
    if summary_text and not args.quiet:
        log.info("summary:\n%s", summary_text)

    if args.summary_only:
        payload: Any = summary
    else:
        payload = result
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        log.info("wrote %s (%d bytes)", args.out, len(text.encode("utf-8")))
    print(text)


if __name__ == "__main__":
    main()
