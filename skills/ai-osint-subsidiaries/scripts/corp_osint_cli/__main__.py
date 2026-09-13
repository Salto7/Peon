#!/usr/bin/env python3
"""corp-osint-cli — deterministic corporate entity lookup (Wikidata-first)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from corp_osint_cli.domain_hints import enrich_domain_hints
from corp_osint_cli.pipeline import run_pipeline
from corp_osint_cli.schema import (
    export_seeds,
    load_json,
    normalize_findings,
    save_entities,
)
from corp_osint_cli.wikidata import fetch_corporate_graph, resolve_company


def cmd_resolve(args: argparse.Namespace) -> int:
    company = " ".join(args.company).strip()
    qid = (args.qid or "").strip()
    resolved = resolve_company(company, qid=qid)
    print(json.dumps(
        {
            "status": resolved.status,
            "company": resolved.company,
            "qid": resolved.qid,
            "label": resolved.label,
            "description": resolved.description,
            "candidates": [
                {
                    "qid": c.qid,
                    "label": c.label,
                    "description": c.description,
                    "match": c.match,
                }
                for c in resolved.candidates
            ],
        },
        indent=2,
    ))
    return 0 if resolved.status == "ok" else 2


def cmd_pipeline(args: argparse.Namespace) -> int:
    company = " ".join(args.company).strip()
    if not company and not (args.qid or "").strip():
        print("company name or --qid required", file=sys.stderr)
        return 2
    code, summary = run_pipeline(
        company=company,
        qid=(args.qid or "").strip(),
        ticker=(args.ticker or "").strip(),
        cik=(args.cik or "").strip(),
        out_dir=Path(args.out_dir),
        domain_hints=not bool(args.no_domain_hints),
        sec=not bool(args.no_sec),
        wikipedia=not bool(args.no_wikipedia),
        merge_predecessors=not bool(args.no_predecessors),
    )
    print(json.dumps(summary, indent=2))
    return code


def cmd_graph(args: argparse.Namespace) -> int:
    qid = (args.qid or "").strip()
    if not qid:
        print("--qid is required", file=sys.stderr)
        return 2
    label = (args.label or "").strip()
    rows = fetch_corporate_graph(qid, root_label=label)
    if args.domain_hints:
        rows = enrich_domain_hints(rows)
    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        print(f"# wrote {args.out} ({len(rows)} rows)", file=sys.stderr)
    print(json.dumps({"count": len(rows), "findings": rows}, indent=2))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    data = load_json(args.input) if args.input else json.loads(sys.stdin.read())
    rows = normalize_findings(data, allow_empty=bool(args.allow_empty))
    print(json.dumps({"count": len(rows), "findings": rows}, indent=2))
    return 0


def cmd_save(args: argparse.Namespace) -> int:
    data = load_json(args.input)
    rows = normalize_findings(data, allow_empty=bool(args.allow_empty))
    payload = save_entities(
        rows,
        company=(args.company or "").strip(),
        qid=(args.qid or "").strip(),
        out_path=Path(args.out),
    )
    print(f"# wrote {args.out} ({len(rows)} findings)", file=sys.stderr)
    print(json.dumps({"count": payload["count"], "out": args.out}, indent=2))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    src = Path(args.input)
    if not src.is_file():
        print(f"missing {src}", file=sys.stderr)
        return 2
    data = json.loads(src.read_text(encoding="utf-8"))
    rows = normalize_findings(data, allow_empty=True)
    counts = export_seeds(
        rows,
        seeds_path=Path(args.seeds_out),
        domains_path=Path(args.domains_out),
    )
    print(f"# wrote {args.seeds_out} ({counts['seeds']} seeds)", file=sys.stderr)
    print(f"# wrote {args.domains_out} ({counts['domains']} domains)", file=sys.stderr)
    print(json.dumps(counts, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="corp-osint-cli",
        description="Deterministic corporate OSINT from Wikidata + SEC + Wikipedia (stdlib-only CLI).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    rs = sub.add_parser("resolve", help="Resolve company name to Wikidata QID")
    rs.add_argument("company", nargs="+", help="Company name")
    rs.add_argument("--qid", default="", help="Skip search; use this QID")
    rs.set_defaults(func=cmd_resolve)

    pl = sub.add_parser("pipeline", help="Full run: resolve → graph → export")
    pl.add_argument("company", nargs="*", help="Company name")
    pl.add_argument("--qid", default="", help="Wikidata QID (skip ambiguous resolve)")
    pl.add_argument("--ticker", default="", help="SEC ticker (optional CIK hint)")
    pl.add_argument("--cik", default="", help="SEC CIK (optional; bypass SEC lookup)")
    pl.add_argument("--out-dir", default=".", help="Output directory")
    pl.add_argument(
        "--no-domain-hints",
        action="store_true",
        help="Do not fill missing domains via Clearbit / DDG (default: fill, capped)",
    )
    pl.add_argument("--domain-hints", action="store_true", help=argparse.SUPPRESS)
    pl.add_argument("--no-sec", action="store_true", help="Skip SEC Exhibit 21 / 10-K")
    pl.add_argument("--no-wikipedia", action="store_true", help="Skip Wikipedia enrichment")
    pl.add_argument("--no-predecessors", action="store_true", help="Do not merge predecessor graphs")
    pl.set_defaults(func=cmd_pipeline)

    gr = sub.add_parser("graph", help="Fetch Wikidata corporate graph for a QID")
    gr.add_argument("--qid", required=True)
    gr.add_argument("--label", default="", help="Root entity label")
    gr.add_argument("--out", default="", help="Write findings array JSON")
    gr.add_argument("--domain-hints", action="store_true")
    gr.set_defaults(func=cmd_graph)

    v = sub.add_parser("validate", help="Validate findings JSON")
    v.add_argument("input", nargs="?", default="", help="File (stdin if omitted)")
    v.add_argument("--allow-empty", action="store_true")
    v.set_defaults(func=cmd_validate)

    s = sub.add_parser("save", help="Validate and write compact corp-entities.json")
    s.add_argument("--company", default="")
    s.add_argument("--qid", default="")
    s.add_argument("--input", required=True)
    s.add_argument("--out", default="corp-entities.json")
    s.add_argument("--allow-empty", action="store_true")
    s.set_defaults(func=cmd_save)

    e = sub.add_parser("export", help="Export corp-seeds.txt + corp-domains.txt")
    e.add_argument("--input", default="corp-entities.json")
    e.add_argument("--seeds-out", default="corp-seeds.txt")
    e.add_argument("--domains-out", default="corp-domains.txt")
    e.set_defaults(func=cmd_export)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
