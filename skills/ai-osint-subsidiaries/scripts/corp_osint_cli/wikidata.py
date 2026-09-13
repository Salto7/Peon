"""Wikidata company resolution and corporate relationship graph."""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from corp_osint_cli.apex import registrable_apex
from corp_osint_cli.http import fetch_json
from corp_osint_cli.schema import norm_qid

_ORG_DESC_HINTS = (
    "company",
    "corporation",
    "business",
    "enterprise",
    "brand",
    "multinational",
    "conglomerate",
    "holding",
    "software company",
    "technology company",
    "retailer",
    "manufacturer",
)


@dataclass
class CompanyCandidate:
    qid: str
    label: str
    description: str
    match: str  # exact | fuzzy


@dataclass
class ResolveResult:
    status: str  # ok | ambiguous | not_found
    company: str
    qid: str = ""
    label: str = ""
    description: str = ""
    candidates: list[CompanyCandidate] = field(default_factory=list)


def _parse_wikidata_time(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    if text.startswith("+"):
        text = text[1:]
    text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return ""


def _is_org_hit(description: str) -> bool:
    desc = (description or "").strip().lower()
    return any(h in desc for h in _ORG_DESC_HINTS)


def search_entities(name: str, *, limit: int = 12) -> list[CompanyCandidate]:
    query = name.strip()
    if not query:
        return []
    data = fetch_json(
        "https://www.wikidata.org/w/api.php?"
        + urllib.parse.urlencode(
            {
                "action": "wbsearchentities",
                "search": query,
                "language": "en",
                "format": "json",
                "limit": limit,
                "type": "item",
            }
        )
    )
    out: list[CompanyCandidate] = []
    q_lower = query.lower()
    for hit in data.get("search") or []:
        qid = norm_qid(str(hit.get("id") or ""))
        label = str(hit.get("label") or "").strip()
        desc = str(hit.get("description") or "").strip()
        if not qid or not label:
            continue
        match = "fuzzy"
        if label.lower() == q_lower:
            match = "exact"
        out.append(CompanyCandidate(qid=qid, label=label, description=desc, match=match))
    return out


def resolve_company(name: str, *, qid: str = "") -> ResolveResult:
    company = name.strip()
    if qid:
        qid = norm_qid(qid)
        labels = batch_entity_labels([qid])
        label = labels.get(qid) or company
        return ResolveResult(
            status="ok",
            company=company or label,
            qid=qid,
            label=label,
        )

    if not company:
        return ResolveResult(status="not_found", company="")

    hits = search_entities(company)
    if not hits:
        return ResolveResult(status="not_found", company=company)

    exact = [h for h in hits if h.match == "exact" and _is_org_hit(h.description)]
    if len(exact) == 1:
        h = exact[0]
        return ResolveResult(
            status="ok",
            company=company,
            qid=h.qid,
            label=h.label,
            description=h.description,
        )

    org_hits = [h for h in hits if _is_org_hit(h.description)]
    fuzzy = []
    q_lower = company.lower()
    for h in org_hits:
        label_l = h.label.lower()
        if q_lower in label_l or label_l in q_lower:
            fuzzy.append(h)

    if len(fuzzy) == 1:
        h = fuzzy[0]
        return ResolveResult(
            status="ok",
            company=company,
            qid=h.qid,
            label=h.label,
            description=h.description,
        )

    candidates = exact or fuzzy or org_hits[:8]
    if not candidates:
        return ResolveResult(status="not_found", company=company)

    return ResolveResult(
        status="ambiguous",
        company=company,
        candidates=candidates[:8],
    )


def batch_entity_labels(qids: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    ids = [norm_qid(q) for q in qids if norm_qid(q)]
    if not ids:
        return out
    for i in range(0, len(ids), 50):
        chunk = ids[i : i + 50]
        data = fetch_json(
            "https://www.wikidata.org/w/api.php?"
            + urllib.parse.urlencode(
                {
                    "action": "wbgetentities",
                    "ids": "|".join(chunk),
                    "props": "labels",
                    "languages": "en",
                    "format": "json",
                }
            )
        )
        for qid in chunk:
            ent = (data.get("entities") or {}).get(qid) or {}
            label = str(((ent.get("labels") or {}).get("en") or {}).get("value") or "").strip()
            if label:
                out[qid] = label
    return out


def _website_from_entity(ent: dict[str, Any]) -> str:
    """Prefer P856 without a language qualifier; collapse regional hosts to apex."""
    claims = (ent.get("claims") or {}).get("P856") or []
    ranked: list[tuple[int, str]] = []
    for claim in claims:
        try:
            val = claim["mainsnak"]["datavalue"]["value"]
        except (KeyError, TypeError):
            continue
        if not (isinstance(val, str) and val.strip()):
            continue
        host = registrable_apex(val)
        if not host:
            continue
        quals = claim.get("qualifiers") or {}
        lang_penalty = 1 if quals.get("P407") else 0
        ranked.append((lang_penalty, host))
    if not ranked:
        return ""
    ranked.sort(key=lambda x: (x[0], len(x[1])))
    return ranked[0][1]


def batch_entity_websites(qids: list[str]) -> dict[str, str]:
    """Fetch P856 official websites for up to 50 QIDs per request."""
    out: dict[str, str] = {}
    ids = [norm_qid(q) for q in qids if norm_qid(q)]
    if not ids:
        return out
    chunk_size = 50
    for i in range(0, len(ids), chunk_size):
        chunk = ids[i : i + chunk_size]
        data = fetch_json(
            "https://www.wikidata.org/w/api.php?"
            + urllib.parse.urlencode(
                {
                    "action": "wbgetentities",
                    "ids": "|".join(chunk),
                    "props": "claims",
                    "format": "json",
                }
            )
        )
        for qid in chunk:
            ent = (data.get("entities") or {}).get(qid) or {}
            site = _website_from_entity(ent)
            if site:
                out[qid] = site
    return out


def _evidence_url(qid: str) -> str:
    return f"https://www.wikidata.org/wiki/{norm_qid(qid)}"


def _claim_item_ids(claims: dict[str, Any], pid: str) -> list[str]:
    out: list[str] = []
    for claim in claims.get(pid) or []:
        try:
            val = claim["mainsnak"]["datavalue"]["value"]
        except (KeyError, TypeError):
            continue
        if isinstance(val, dict) and val.get("entity-type") == "item":
            q = norm_qid(str(val.get("id") or ""))
            if q:
                out.append(q)
    return out


def _claim_items_with_start(claims: dict[str, Any], pid: str) -> list[tuple[str, str]]:
    """Return (qid, start_date) from statement qualifiers P580."""
    out: list[tuple[str, str]] = []
    for claim in claims.get(pid) or []:
        try:
            val = claim["mainsnak"]["datavalue"]["value"]
        except (KeyError, TypeError):
            continue
        if not (isinstance(val, dict) and val.get("entity-type") == "item"):
            continue
        q = norm_qid(str(val.get("id") or ""))
        if not q:
            continue
        start = ""
        for qual in (claim.get("qualifiers") or {}).get("P580") or []:
            try:
                start = _parse_wikidata_time(str(qual["datavalue"]["value"]["time"]))
            except (KeyError, TypeError):
                continue
        out.append((q, start))
    return out


def batch_get_entities(qids: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    ids = [norm_qid(q) for q in qids if norm_qid(q)]
    for i in range(0, len(ids), 50):
        chunk = ids[i : i + 50]
        data = fetch_json(
            "https://www.wikidata.org/w/api.php?"
            + urllib.parse.urlencode(
                {
                    "action": "wbgetentities",
                    "ids": "|".join(chunk),
                    "props": "labels|claims|sitelinks",
                    "languages": "en",
                    "format": "json",
                }
            )
        )
        for qid in chunk:
            ent = (data.get("entities") or {}).get(qid)
            if ent:
                out[qid] = ent
    return out


def _label_from_entity(ent: dict[str, Any], fallback: str = "") -> str:
    label = str(((ent.get("labels") or {}).get("en") or {}).get("value") or "").strip()
    return label or fallback


def reverse_parent_children(parent_qid: str, *, limit: int = 50) -> list[str]:
    """Entities that list this QID as parent (P749) via CirrusSearch."""
    qid = norm_qid(parent_qid)
    if not qid:
        return []
    data = fetch_json(
        "https://www.wikidata.org/w/api.php?"
        + urllib.parse.urlencode(
            {
                "action": "query",
                "list": "search",
                "srsearch": f"haswbstatement:P749={qid}",
                "srnamespace": "0",
                "srlimit": limit,
                "format": "json",
            }
        )
    )
    out: list[str] = []
    for hit in (data.get("query") or {}).get("search") or []:
        title = str(hit.get("title") or "").strip().upper()
        if title.startswith("Q") and title[1:].isdigit() and title != qid:
            out.append(title)
    return out


def _related_from_claims(claims: dict[str, Any]) -> list[tuple[str, str, str]]:
    related: list[tuple[str, str, str]] = []
    for item_qid in _claim_item_ids(claims, "P355"):
        related.append((item_qid, "subsidiary", ""))
    for item_qid in _claim_item_ids(claims, "P749"):
        related.append((item_qid, "parent", ""))
    for item_qid, start in _claim_items_with_start(claims, "P1830"):
        related.append((item_qid, "acquisition", start))
    for item_qid in _claim_item_ids(claims, "P1365"):
        related.append((item_qid, "predecessor", ""))
    for item_qid in _claim_item_ids(claims, "P155"):
        related.append((item_qid, "predecessor", ""))
    for item_qid in _claim_item_ids(claims, "P1366"):
        related.append((item_qid, "related entity", ""))
    return related


def _row_from_entity(
    *,
    ent: dict[str, Any],
    qid: str,
    relationship: str,
    start: str = "",
    fallback_label: str = "",
) -> dict[str, str]:
    row: dict[str, str] = {
        "entity": _label_from_entity(ent, fallback_label or qid),
        "qid": qid,
        "relationship": relationship,
        "domain": _website_from_entity(ent),
        "source": "wikidata",
        "evidence_url": _evidence_url(qid),
        "confidence": "high",
    }
    if start and relationship == "acquisition":
        row["start_date"] = start
    return row


def fetch_corporate_graph(
    company_qid: str,
    *,
    root_label: str = "",
    merge_predecessors: bool = True,
) -> list[dict[str, str]]:
    """Return finding rows from Wikidata entity claims (MediaWiki API, no SPARQL)."""
    qid = norm_qid(company_qid)
    if not qid:
        return []

    root_ent = batch_get_entities([qid]).get(qid)
    if not root_ent:
        return []

    claims = root_ent.get("claims") or {}
    related = _related_from_claims(claims)
    for child_qid in reverse_parent_children(qid):
        related.append((child_qid, "subsidiary", ""))

    pred_qids = [item for item, rel, _ in related if rel == "predecessor"]
    extra_qids: list[str] = []
    if merge_predecessors:
        extra_qids = pred_qids[:]

    all_qids = [qid] + [r[0] for r in related] + extra_qids
    entities = batch_get_entities(all_qids)

    if merge_predecessors:
        for pred_qid in pred_qids:
            pred_ent = entities.get(pred_qid) or {}
            for item_qid, rel, start in _related_from_claims(pred_ent.get("claims") or {}):
                if item_qid == qid:
                    continue
                nested_rel = rel
                if rel == "parent":
                    continue
                if rel == "subsidiary":
                    nested_rel = "related entity"
                related.append((item_qid, nested_rel, start))
        more = [r[0] for r in related if r[0] not in entities]
        if more:
            entities.update(batch_get_entities(more))

    root_name = root_label or _label_from_entity(root_ent, qid)
    rows: list[dict[str, str]] = [
        _row_from_entity(
            ent=root_ent,
            qid=qid,
            relationship="parent",
            fallback_label=root_name,
        )
    ]
    seen: set[str] = {qid.lower()}
    for item_qid, rel, start in related:
        if item_qid.lower() in seen:
            continue
        seen.add(item_qid.lower())
        ent = entities.get(item_qid) or {}
        rows.append(
            _row_from_entity(ent=ent, qid=item_qid, relationship=rel, start=start)
        )
    return rows


def entity_cik_ticker(ent: dict[str, Any]) -> tuple[str, str]:
    from corp_osint_cli.sec import cik_from_wikidata_entity, ticker_from_wikidata_entity

    return cik_from_wikidata_entity(ent), ticker_from_wikidata_entity(ent)


def enwiki_title(ent: dict[str, Any]) -> str:
    from corp_osint_cli.wikipedia import enwiki_title_from_entity

    return enwiki_title_from_entity(ent)
