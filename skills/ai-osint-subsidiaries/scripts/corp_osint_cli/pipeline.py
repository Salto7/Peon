"""Pipeline orchestration."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from corp_osint_cli.apex import prefer_shorter_apex
from corp_osint_cli.domain_hints import enrich_domain_hints
from corp_osint_cli.schema import export_seeds, merge_rows, normalize_findings, save_entities
from corp_osint_cli.sec import (
    dump_meta,
    fetch_10k_acquisition_rows,
    fetch_exhibit_21_rows,
    lookup_cik,
    pad_cik,
)
from corp_osint_cli.wikidata import (
    ResolveResult,
    batch_get_entities,
    entity_cik_ticker,
    enwiki_title,
    fetch_corporate_graph,
    resolve_company,
)
from corp_osint_cli.wikipedia import wikipedia_rows


def run_pipeline(
    *,
    company: str,
    qid: str = "",
    ticker: str = "",
    cik: str = "",
    out_dir: Path,
    domain_hints: bool = True,
    sec: bool = True,
    wikipedia: bool = True,
    merge_predecessors: bool = True,
    md_path: Path | None = None,
) -> tuple[int, dict]:
    """Run full lookup. Returns (exit_code, summary dict)."""
    out_dir = Path(out_dir)
    raw_dir = out_dir / "raw" / "corp-osint"
    raw_dir.mkdir(parents=True, exist_ok=True)

    resolved = resolve_company(company, qid=qid)
    resolve_path = raw_dir / "resolve.json"
    resolve_path.write_text(
        json.dumps(_resolve_payload(resolved), indent=2) + "\n",
        encoding="utf-8",
    )

    if resolved.status == "ambiguous":
        return 2, {
            "status": "ambiguous",
            "company": resolved.company,
            "candidates": [asdict(c) for c in resolved.candidates],
            "resolve_path": str(resolve_path),
            "hint": "Re-run with --qid Q… from candidates",
        }

    if resolved.status == "not_found":
        return 2, {
            "status": "not_found",
            "company": resolved.company,
            "resolve_path": str(resolve_path),
        }

    assert resolved.qid
    root_ent = batch_get_entities([resolved.qid]).get(resolved.qid) or {}
    graph = fetch_corporate_graph(
        resolved.qid,
        root_label=resolved.label,
        merge_predecessors=merge_predecessors,
    )
    graph_path = raw_dir / "wikidata-graph.json"
    graph_path.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")

    extra: dict = {}
    wiki_rows: list[dict[str, str]] = []
    wiki_site = ""
    if wikipedia:
        title = enwiki_title(root_ent)
        if title:
            wiki_rows, wiki_site = wikipedia_rows(title=title)
            (raw_dir / "wikipedia.json").write_text(
                json.dumps({"title": title, "website": wiki_site, "findings": wiki_rows}, indent=2)
                + "\n",
                encoding="utf-8",
            )
            extra["wikipedia_title"] = title
        if wiki_site and graph:
            graph[0]["domain"] = prefer_shorter_apex(graph[0].get("domain") or "", wiki_site)

    sec_rows: list[dict[str, str]] = []
    acq_rows: list[dict[str, str]] = []
    selected_cik = pad_cik(cik)
    selected_ticker = (ticker or "").strip().upper()
    if sec:
        cik_wd, ticker_wd = entity_cik_ticker(root_ent)
        sec_title = ""
        if not selected_cik:
            selected_cik, sec_title = lookup_cik(
                ticker=selected_ticker or ticker_wd,
                company=resolved.label or company,
            )
        if not selected_cik:
            selected_cik = cik_wd
        if not selected_ticker:
            selected_ticker = ticker_wd
        extra["cik"] = pad_cik(selected_cik)
        extra["ticker"] = selected_ticker
        extra["sec_title"] = sec_title
        filing: dict[str, str] = {}
        if selected_cik:
            try:
                sec_rows, filing = fetch_exhibit_21_rows(
                    cik=selected_cik, company=resolved.label or company
                )
                extra["sec"] = dump_meta(filing)
                (raw_dir / "sec-exhibit-21.json").write_text(
                    json.dumps({"filing": dump_meta(filing), "findings": sec_rows}, indent=2)
                    + "\n",
                    encoding="utf-8",
                )
                acq_rows = fetch_10k_acquisition_rows(
                    cik=selected_cik, filing=filing or None
                )
                (raw_dir / "sec-10k-acquisitions.json").write_text(
                    json.dumps(acq_rows, indent=2) + "\n",
                    encoding="utf-8",
                )
            except Exception as exc:
                extra["sec_error"] = str(exc)[:300]

    combined = merge_rows(graph + wiki_rows + acq_rows + sec_rows)

    if domain_hints:
        combined = enrich_domain_hints(combined)

    rows = normalize_findings(combined, allow_empty=True)
    company_label = resolved.label or resolved.company

    entities_path = out_dir / "corp-entities.json"
    raw_path = raw_dir / "entities.full.json"
    if md_path is None:
        md_path = (
            out_dir.parent / "findings" / "corp-osint.md"
            if out_dir.name == "workspace"
            else out_dir / "corp-osint.md"
        )
    save_entities(
        rows,
        company=company_label,
        qid=resolved.qid,
        out_path=entities_path,
        extra={k: v for k, v in extra.items() if v},
        raw_path=raw_path,
        md_path=md_path,
    )

    seeds_path = out_dir / "corp-seeds.txt"
    domains_path = out_dir / "corp-domains.txt"
    counts = export_seeds(rows, seeds_path=seeds_path, domains_path=domains_path)

    return 0, {
        "status": "ok",
        "company": company_label,
        "qid": resolved.qid,
        "cik": extra.get("cik") or "",
        "count": len(rows),
        "wikidata": len(graph),
        "wikipedia": len(wiki_rows),
        "sec_exhibit_21": len(sec_rows),
        "sec_10k_acquisitions": len(acq_rows),
        "entities_path": str(entities_path),
        "raw_path": str(raw_path),
        "md_path": str(md_path),
        "seeds_path": str(seeds_path),
        "domains_path": str(domains_path),
        "resolve_path": str(resolve_path),
        "graph_path": str(graph_path),
        **counts,
    }


def _resolve_payload(resolved: ResolveResult) -> dict:
    payload: dict = {
        "status": resolved.status,
        "company": resolved.company,
        "qid": resolved.qid,
        "label": resolved.label,
        "description": resolved.description,
    }
    if resolved.candidates:
        payload["candidates"] = [asdict(c) for c in resolved.candidates]
    return payload
