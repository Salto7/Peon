"""Finding schema, validation, merge, and export helpers."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from corp_osint_cli.apex import is_domain, prefer_shorter_apex, registrable_apex

RELATIONSHIPS = frozenset(
    {
        "parent",
        "subsidiary",
        "acquisition",
        "merger",
        "predecessor",
        "former subsidiary",
        "former parent",
        "merger subsidiary",
        "acquisition vehicle",
        "related entity",
    }
)
RELATIONSHIP_ALIASES = {
    "daughter company": "subsidiary",
    "daughter": "subsidiary",
    "sub": "subsidiary",
    "acquired": "acquisition",
    "acquire": "acquisition",
    "business acquisition target": "acquisition",
    "acquisition target": "acquisition",
    "merged": "merger",
    "merged company": "merger",
    "spin-off": "former subsidiary",
    "spinoff": "former subsidiary",
    "spun off": "former subsidiary",
    "divested": "former subsidiary",
    "legacy": "predecessor",
}
SOURCES = frozenset(
    {
        "ai",
        "wikidata",
        "wikipedia",
        "sec_exhibit_21",
        "sec_10k",
        "duckduckgo",
        "clearbit",
        "handoff",
    }
)
CONFIDENCE = frozenset({"high", "medium", "low"})
SOURCE_RANK = {
    "sec_exhibit_21": 50,
    "sec_10k": 40,
    "wikidata": 35,
    "wikipedia": 30,
    "clearbit": 15,
    "duckduckgo": 10,
    "ai": 8,
    "handoff": 5,
}
COMPACT_KEYS = ("entity", "relationship", "domain", "source")
HANDOFF_FORMAT = "corp-osint/v3"
_REL_RANK = {
    "parent": 90,
    "subsidiary": 70,
    "acquisition": 65,
    "predecessor": 60,
    "merger": 55,
    "former subsidiary": 50,
    "former parent": 50,
    "merger subsidiary": 45,
    "acquisition vehicle": 45,
    "related entity": 10,
}

_DOMAIN_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$",
    re.I,
)
_QID_RE = re.compile(r"^Q\d+$", re.I)


def norm_domain(raw: str) -> str:
    return registrable_apex(raw) or ""


def norm_qid(raw: str) -> str:
    q = (raw or "").strip().upper()
    if q.startswith("WD:"):
        q = q[3:]
    return q


def entity_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def normalize_relationship(raw: str) -> str:
    rel = re.sub(r"\s+", " ", (raw or "").strip().lower())
    rel = RELATIONSHIP_ALIASES.get(rel, rel)
    if rel in RELATIONSHIPS:
        return rel
    return "related entity"


def _is_http_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def compact_row(row: dict[str, str]) -> dict[str, str]:
    """Handoff row: entity, relationship, domain, source (evidence URL)."""
    url = str(row.get("evidence_url") or "").strip()
    source = str(row.get("source") or "").strip()
    if _is_http_url(url):
        source_url = url
    elif _is_http_url(source):
        source_url = source
    else:
        source_url = ""
    return {
        "entity": str(row.get("entity") or "").strip(),
        "relationship": str(row.get("relationship") or "").strip(),
        "domain": str(row.get("domain") or "").strip(),
        "source": source_url,
    }


def compact_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [compact_row(r) for r in rows if (r.get("entity") or r.get("domain"))]


def _md_cell(value: str) -> str:
    return (value or "").replace("|", "\\|").replace("\n", " ")


def write_phase_markdown(
    rows: list[dict[str, str]],
    path: Path,
    *,
    title: str,
    company: str = "",
) -> None:
    compact = compact_rows(rows)
    lines = [f"# {title}", ""]
    if company:
        lines.append(f"Company: {company}")
    lines.append(f"Count: {len(compact)}")
    lines.extend(
        [
            "",
            "| Entity | Relationship | Domain | Source |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in compact:
        lines.append(
            "| "
            + " | ".join(
                (
                    _md_cell(row["entity"]),
                    _md_cell(row["relationship"]),
                    _md_cell(row["domain"]),
                    _md_cell(row["source"]),
                )
            )
            + " |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_finding(obj: dict[str, Any]) -> dict[str, str]:
    if not isinstance(obj, dict) or not obj:
        raise ValueError("each entry must be a non-empty object")

    entity = str(obj.get("entity") or "").strip()
    relationship = normalize_relationship(str(obj.get("relationship") or ""))
    domain = norm_domain(str(obj.get("domain") or ""))
    qid = norm_qid(str(obj.get("qid") or ""))
    raw_source = str(obj.get("source") or "").strip()
    evidence_url = str(obj.get("evidence_url") or "").strip()
    origin = str(obj.get("origin") or obj.get("source_type") or "").strip().lower()
    start_date = str(obj.get("start_date") or "").strip()
    confidence = str(obj.get("confidence") or "high").strip().lower()
    notes = str(obj.get("notes") or "").strip()

    if _is_http_url(raw_source):
        if not evidence_url:
            evidence_url = raw_source
        source = origin or "handoff"
    else:
        source = (raw_source or origin or "handoff").strip().lower()

    if not entity:
        raise ValueError("missing entity")
    if relationship not in RELATIONSHIPS:
        raise ValueError(f"invalid relationship {relationship!r}")
    if source not in SOURCES:
        raise ValueError(f"invalid source {source!r}; expected one of {sorted(SOURCES)}")
    if evidence_url and not _is_http_url(evidence_url):
        raise ValueError("evidence_url must be an http(s) URL")
    if not evidence_url and source != "handoff":
        raise ValueError("evidence_url must be an http(s) URL")
    if qid and not _QID_RE.match(qid):
        raise ValueError(f"invalid qid {qid!r}")
    if domain and not is_domain(domain) and not _DOMAIN_RE.match(domain):
        raise ValueError(f"invalid domain apex {domain!r}")
    if confidence not in CONFIDENCE:
        raise ValueError(f"invalid confidence {confidence!r}")

    row: dict[str, str] = {
        "entity": entity,
        "relationship": relationship,
        "domain": domain,
        "qid": qid,
        "source": source,
        "evidence_url": evidence_url,
        "confidence": confidence,
    }
    if start_date:
        row["start_date"] = start_date
    if notes:
        row["notes"] = notes
    return row


def normalize_findings(raw: Any, *, allow_empty: bool = False) -> list[dict[str, str]]:
    if isinstance(raw, dict) and "findings" in raw:
        raw = raw["findings"]
    if not isinstance(raw, list):
        raise ValueError("expected a JSON array of findings")
    out: list[dict[str, str]] = []
    for i, item in enumerate(raw):
        try:
            row = parse_finding(item)
        except ValueError as exc:
            raise ValueError(f"entry {i}: {exc}") from exc
        out.append(row)
    merged = merge_rows(out)
    if not merged and not allow_empty:
        raise ValueError("empty findings array")
    return merged


def merge_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Dedupe by entity name; keep higher-rank source / relationship / shorter domain."""
    by_key: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for row in rows:
        key = entity_key(row.get("entity") or "")
        if not key:
            continue
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = dict(row)
            order.append(key)
            continue
        by_key[key] = _prefer_row(existing, row)
    return [by_key[k] for k in order]


def _prefer_row(a: dict[str, str], b: dict[str, str]) -> dict[str, str]:
    a_rank = SOURCE_RANK.get(a.get("source") or "", 0)
    b_rank = SOURCE_RANK.get(b.get("source") or "", 0)
    winner = dict(a)
    loser = dict(b)
    if b_rank > a_rank:
        winner, loser = dict(b), dict(a)
    elif b_rank == a_rank:
        if _REL_RANK.get(b.get("relationship") or "", 0) > _REL_RANK.get(
            a.get("relationship") or "", 0
        ):
            winner, loser = dict(b), dict(a)
    domain = prefer_shorter_apex(winner.get("domain") or "", loser.get("domain") or "")
    if domain:
        winner["domain"] = domain
    if not winner.get("qid") and loser.get("qid"):
        winner["qid"] = loser["qid"]
    if not winner.get("start_date") and loser.get("start_date"):
        winner["start_date"] = loser["start_date"]
    if not winner.get("notes") and loser.get("notes"):
        winner["notes"] = loser["notes"]
    return winner


def load_json(path: str | None) -> Any:
    text = Path(path).read_text(encoding="utf-8") if path else ""
    if not text.strip():
        raise ValueError("empty JSON input")
    return json.loads(text)


def full_envelope(
    rows: list[dict[str, str]],
    *,
    company: str,
    qid: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "company": company,
        "qid": qid,
        "count": len(rows),
        "findings": rows,
        "format": HANDOFF_FORMAT,
    }
    if extra:
        payload.update(extra)
    return payload


def save_entities(
    rows: list[dict[str, str]],
    *,
    company: str,
    qid: str = "",
    out_path: Path,
    extra: dict[str, Any] | None = None,
    raw_path: Path | None = None,
    md_path: Path | None = None,
) -> dict[str, Any]:
    """Write compact handoff JSON; keep provenance in raw_path when given."""
    compact = compact_rows(rows)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(compact, indent=2) + "\n", encoding="utf-8")
    envelope = full_envelope(rows, company=company, qid=qid, extra=extra)
    if raw_path is None:
        raw_path = out_path.parent / "raw" / "corp-osint" / "entities.full.json"
    raw_path = Path(raw_path)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")
    if md_path is not None:
        write_phase_markdown(
            rows, Path(md_path), title="Corporate OSINT", company=company
        )
    envelope["compact_path"] = str(out_path)
    envelope["raw_path"] = str(raw_path)
    return envelope


def export_seeds(rows: list[dict[str, str]], *, seeds_path: Path, domains_path: Path) -> dict[str, int]:
    seeds = list(dict.fromkeys(r["entity"] for r in rows if r.get("entity")))
    domains = list(dict.fromkeys(d for r in rows if (d := (r.get("domain") or "").strip())))
    seeds_path.parent.mkdir(parents=True, exist_ok=True)
    seeds_path.write_text("\n".join(seeds) + ("\n" if seeds else ""), encoding="utf-8")
    domains_path.write_text("\n".join(domains) + ("\n" if domains else ""), encoding="utf-8")
    return {"seeds": len(seeds), "domains": len(domains)}
