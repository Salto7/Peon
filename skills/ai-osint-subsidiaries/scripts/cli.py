#!/usr/bin/env python3
"""Corporate recon workflow plus legacy SEC/AI subcommands."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from corp_osint_cli.workflow import (
    CorporateReconState,
    CorporateReconWorkflow,
)
from lib.prompt import (
    format_model_answer_envelope,
    load_json,
    merge_ai_into_base,
    parse_ai_findings,
    render_prompt,
    rows_from,
)
from lib.schema import export_seeds, save_entities
from lib.sec import run_pipeline


def _ws(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "workspace", None) or "workspace")


def _full(ws: Path) -> Path:
    return ws / "raw" / "ai-osint-subsidiaries" / "entities.full.json"


def _load(path: Path) -> tuple[list[dict], dict]:
    data = load_json(path)
    if isinstance(data, dict):
        meta = {k: v for k, v in data.items() if k != "findings"}
        rows = rows_from(data)
    else:
        meta, rows = {}, rows_from(data)
    out = [{str(k): str(v) if v is not None else "" for k, v in r.items()} for r in rows]
    return out, meta


def cmd_sec(args: argparse.Namespace) -> int:
    company = " ".join(args.company).strip()
    if not company and not (args.cik or "").strip() and not (args.ticker or "").strip():
        print("company name, --ticker, or --cik required", file=sys.stderr)
        return 2
    code, summary = run_pipeline(
        company=company,
        cik=(args.cik or "").strip(),
        ticker=(args.ticker or "").strip(),
        out_dir=Path(args.out_dir),
    )
    print(json.dumps(summary, indent=2))
    return code


def cmd_workflow(args: argparse.Namespace) -> int:
    """Run the ordered discovery -> canonical handoff -> domain-enum graph."""

    def subsidiary_runner(
        state: CorporateReconState, out_dir: Path
    ) -> tuple[int, dict]:
        return run_pipeline(
            company=state.company,
            cik=state.cik,
            ticker=state.ticker,
            out_dir=out_dir,
        )

    workflow = CorporateReconWorkflow(
        Path(args.workspace),
        subsidiary_runner=subsidiary_runner,
    )
    try:
        state = workflow.run(
            company=" ".join(args.company).strip(),
            ticker=(args.ticker or "").strip(),
            cik=(args.cik or "").strip(),
            qid=(args.qid or "").strip(),
            resume=not bool(args.no_resume),
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(state.to_dict(), indent=2))
    return 0 if state.status == "completed" else 2


def cmd_prompt(args: argparse.Namespace) -> int:
    ws = _ws(args)
    path = Path(args.input) if args.input else _full(ws)
    try:
        rows, meta = _load(path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"failed to read SEC output: {exc}", file=sys.stderr)
        return 2
    company = (args.company or meta.get("company") or "").strip()
    cik = str(meta.get("cik") or "")
    try:
        text = render_prompt(company=company, cik=cik, rows=rows)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    # Host runtime detects PEON_MODEL_ANSWER, calls the LLM, writes ai-domains.json,
    # then auto-runs merge-ai. Agents must not script the answer.
    print(format_model_answer_envelope(text, workspace=str(ws)))
    return 0


def cmd_merge_ai(args: argparse.Namespace) -> int:
    """Apply the AI JSON array answer into corp-entities.json."""
    ws = _ws(args)
    try:
        base_rows, meta = _load(Path(args.base) if args.base else _full(ws))
        ai_data = load_json(args.ai)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"failed to read input: {exc}", file=sys.stderr)
        return 2
    ai_rows = parse_ai_findings(ai_data)
    if not ai_rows:
        print("AI JSON had no usable rows", file=sys.stderr)
        return 2
    if not any(r.get("domain") for r in ai_rows):
        print("AI JSON had no domains — refusing merge", file=sys.stderr)
        return 2
    merged = merge_ai_into_base(base_rows, ai_rows)
    company = (args.company or meta.get("company") or "").strip()
    md = (
        ws.parent / "findings" / "ai-osint-subsidiaries.md"
        if ws.name == "workspace"
        else ws / "ai-osint-subsidiaries.md"
    )
    extra = {
        k: v
        for k, v in meta.items()
        if k not in {"generated_at", "count", "findings", "format"}
    }
    extra["ai_merged"] = True
    save_entities(
        merged,
        company=company,
        out_path=ws / "corp-entities.json",
        extra=extra,
        raw_path=_full(ws),
        md_path=md,
    )
    counts = export_seeds(
        merged,
        seeds_path=ws / "corp-seeds.txt",
        domains_path=ws / "corp-domains.txt",
    )
    gap = ws / "raw" / "ai-osint-subsidiaries" / "ai-domains.json"
    gap.parent.mkdir(parents=True, exist_ok=True)
    gap.write_text(json.dumps(ai_data, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "ok",
                "company": company,
                "count": len(merged),
                "domains": counts["domains"],
                "entities_path": str(ws / "corp-entities.json"),
            },
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(argv) if argv is not None else sys.argv[1:]

    p = argparse.ArgumentParser(prog="ai-osint-subsidiaries")
    sub = p.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser(
        "workflow",
        help="Ordered resumable corporate discovery then domain enumeration",
    )
    w.add_argument("company", nargs="+")
    w.add_argument("--ticker", default="")
    w.add_argument("--cik", default="")
    w.add_argument("--qid", default="")
    w.add_argument("--workspace", default="workspace")
    w.add_argument("--no-resume", action="store_true")
    w.set_defaults(func=cmd_workflow)

    s = sub.add_parser("sec", help="Step 1: *.sec.gov Exhibit 21 inventory")
    s.add_argument("company", nargs="*")
    s.add_argument("--cik", default="")
    s.add_argument("--ticker", default="")
    s.add_argument("--out-dir", default="workspace")
    s.set_defaults(func=cmd_sec)

    pl = sub.add_parser("pipeline", help="Alias of sec")
    pl.add_argument("company", nargs="*")
    pl.add_argument("--cik", default="")
    pl.add_argument("--ticker", default="")
    pl.add_argument("--out-dir", default="workspace")
    pl.set_defaults(func=cmd_sec)

    pr = sub.add_parser("prompt", help="Step 2: print one AI domain prompt for all SEC entities")
    pr.add_argument("--workspace", default="workspace")
    pr.add_argument("--input", default="")
    pr.add_argument("--company", default="")
    pr.set_defaults(func=cmd_prompt)

    m = sub.add_parser("merge-ai", help="Apply AI domain JSON array into handoff")
    m.add_argument("--ai", required=True)
    m.add_argument("--base", default="")
    m.add_argument("--workspace", default="workspace")
    m.add_argument("--company", default="")
    m.set_defaults(func=cmd_merge_ai)

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
