"""SEC-only subsidiary adapter (skill-local corp_osint_cli)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from corp_osint_cli.schema import (  # noqa: E402
    export_seeds,
    merge_rows,
    normalize_findings,
    save_entities,
)
from corp_osint_cli.sec import (  # noqa: E402,F401
    fetch_exhibit_21_rows,
    find_filers,
    latest_10k,
    pad_cik,
    parse_exhibit_21_html,
)


def run_pipeline(
    *,
    company: str,
    cik: str = "",
    ticker: str = "",
    out_dir: Path,
    md_path: Path | None = None,
) -> tuple[int, dict]:
    """Pull parent and Exhibit 21 subsidiaries from public SEC filings."""
    out_dir = Path(out_dir)
    raw_dir = out_dir / "raw" / "ai-osint-subsidiaries"
    raw_dir.mkdir(parents=True, exist_ok=True)
    company = (company or "").strip()
    cik = pad_cik(cik)
    title = company

    if not cik:
        hits = find_filers(ticker=ticker, company=company)
        if not hits:
            return 2, {
                "status": "not_found",
                "company": company,
                "hint": "No SEC CIK match; pass --cik or a clearer filer name/ticker",
            }
        if len(hits) > 1:
            return 2, {
                "status": "ambiguous",
                "company": company,
                "candidates": hits[:20],
                "hint": "Re-run with --cik from candidates",
            }
        cik, title = hits[0]["cik"], hits[0]["title"]

    try:
        filing_probe = latest_10k(cik)
        sec_rows, filing_meta = fetch_exhibit_21_rows(
            cik=cik, company=filing_probe.get("name") or title or company
        )
        title = filing_probe.get("name") or title or company
    except Exception as exc:
        return 2, {
            "status": "error",
            "company": company,
            "cik": cik,
            "error": str(exc)[:300],
        }

    (raw_dir / "sec-exhibit-21.json").write_text(
        json.dumps(
            {"filing": {k: v for k, v in filing_meta.items() if v}, "findings": sec_rows},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    parent = {
        "entity": title or company,
        "relationship": "parent",
        "domain": "",
        "source": "sec_exhibit_21",
        "evidence_url": filing_meta.get("exhibit_21_url")
        or f"https://data.sec.gov/submissions/CIK{pad_cik(cik)}.json",
        "confidence": "high",
    }
    rows = normalize_findings(merge_rows([parent] + sec_rows), allow_empty=True)
    entities_path = out_dir / "corp-entities.json"
    raw_path = raw_dir / "entities.full.json"
    if md_path is None:
        md_path = (
            out_dir.parent / "findings" / "ai-osint-subsidiaries.md"
            if out_dir.name == "workspace"
            else out_dir / "ai-osint-subsidiaries.md"
        )
    save_entities(
        rows,
        company=title or company,
        out_path=entities_path,
        extra={"cik": pad_cik(cik), "sec": {k: v for k, v in filing_meta.items() if v}},
        raw_path=raw_path,
        md_path=md_path,
    )
    counts = export_seeds(
        rows,
        seeds_path=out_dir / "corp-seeds.txt",
        domains_path=out_dir / "corp-domains.txt",
    )
    return 0, {
        "status": "ok",
        "company": title or company,
        "cik": pad_cik(cik),
        "count": len(rows),
        "sec_exhibit_21": len(sec_rows),
        "entities_path": str(entities_path),
        "raw_path": str(raw_path),
        "md_path": str(md_path),
        **counts,
    }
