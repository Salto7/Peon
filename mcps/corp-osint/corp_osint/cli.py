#!/usr/bin/env python3
"""
corp-OSINT — CLI entrypoint.

Examples:
  uv run python -m corp_osint "Example Corp" --domain example.com --out report.json
  uv run python -m corp_osint --list-plugins
  uv run python -m corp_osint --plugin azure_tenant --domain microsoft.com
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from .config import STAGE_NAMES
from .log import emit, get_logger, setup_logging
from .plugins import list_plugins
from .plugins.pipeline import CorpOsintPipeline
from .report import ensure_summary
from .runtime import run_pipeline, run_plugin

log = get_logger("cli")
_STAGE_HELP = ", ".join(STAGE_NAMES)


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="corp-osint",
        description="Corporate OSINT — plugin-based pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Stages for --skip/--only: {_STAGE_HELP}",
    )
    ap.add_argument("-v", "--verbose", action="count", default=0, help="-v DEBUG, -vv more detail")
    ap.add_argument("-q", "--quiet", action="store_true", help="Warnings/errors only")
    ap.add_argument("company", nargs="?", help="Company name (required for full pipeline)")
    ap.add_argument("--cik")
    ap.add_argument(
        "--ticker",
        metavar="STOCK_TICKER_SYMBOL",
        help="Stock market ticker symbol used to identify the SEC company",
    )
    ap.add_argument("--domain")
    ap.add_argument("--company", dest="company_opt", help="Alias for positional company")
    ap.add_argument("--list-plugins", action="store_true", help="List registered plugins")
    ap.add_argument(
        "--plugin",
        help="Run a single plugin by name (see --list-plugins). Default: full pipeline",
    )
    ap.add_argument("--skip", metavar="STAGES", help=f"Disable stages ({_STAGE_HELP})")
    ap.add_argument("--only", metavar="STAGES", help="Run only these stages")
    ap.add_argument("--tenant-id", help="Entra tenant GUID (tenant_domains / dangling)")
    ap.add_argument("--out", help="Write JSON output path")
    ap.add_argument("--summary-only", action="store_true", help="Print only compact summary JSON")
    ap.add_argument(
        "--proxy",
        metavar="URL",
        help="HTTP/HTTPS/SOCKS proxy (default: CORP_OSINT_PROXY / *_PROXY env)",
    )
    ap.add_argument("--direct", action="store_true", help="Disable proxy (ignore env proxies)")
    ap.add_argument(
        "--no-proxy",
        dest="no_proxy_hosts",
        metavar="HOSTS",
        help="Comma-separated hosts that bypass the proxy (also NO_PROXY env)",
    )
    return ap


def _args_to_values(args: argparse.Namespace) -> dict[str, Any]:
    """Map argparse namespace → flat values dict for corp_osint.runtime."""
    company = args.company_opt or args.company
    values: dict[str, Any] = {}
    if company:
        values["company"] = company
    for key in ("domain", "cik", "ticker", "skip", "only", "tenant_id"):
        val = getattr(args, key, None)
        if val:
            values[key] = val
    if args.direct:
        values["direct"] = True
    elif args.proxy is not None:
        values["proxy"] = args.proxy
    if args.no_proxy_hosts is not None:
        values["no_proxy"] = args.no_proxy_hosts
    return values


def _resolve_out_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path.resolve()
    base = Path.cwd().resolve()
    resolved = (base / path).resolve()
    if not resolved.is_relative_to(base):
        raise SystemExit(f"--out path escapes working directory: {raw}")
    return resolved


def _log_plugins() -> None:
    rows = list_plugins() + [
        {"name": CorpOsintPipeline.name, "description": CorpOsintPipeline.description}
    ]
    lines = []
    for row in sorted(rows, key=lambda r: r["name"]):
        raw = (row.get("description") or "").strip()
        desc = raw.splitlines()[0][:80] if raw else ""
        lines.append(f"{row['name']:20}  {desc}")
    emit("\n".join(lines))


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    setup_logging(verbosity=args.verbose, quiet=args.quiet)

    if args.list_plugins:
        _log_plugins()
        return

    values = _args_to_values(args)
    try:
        if args.plugin:
            result = asyncio.run(run_plugin(args.plugin, values))
        else:
            if not values.get("company"):
                raise SystemExit("company name required for full pipeline")
            result = asyncio.run(run_pipeline(values))
    except KeyboardInterrupt:
        log.error("interrupted")
        raise SystemExit(130) from None
    except Exception as e:  # noqa: BLE001
        log.exception("fatal: %s", e)
        raise SystemExit(1) from e

    if not isinstance(result, dict):
        result = {"result": result}
    result = ensure_summary(result)

    summary_text = result.get("summary_text")
    if summary_text and not args.quiet:
        log.info("summary:\n%s", summary_text)

    payload: Any = result.get("summary") if args.summary_only else result
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.out:
        out_path = _resolve_out_path(args.out)
        out_path.write_text(text + "\n", encoding="utf-8")
        log.info("wrote %s (%d bytes)", out_path, len(text.encode("utf-8")))
    emit(text)


if __name__ == "__main__":
    main()
