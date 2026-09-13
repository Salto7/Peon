"""Optional domain hints from public APIs (medium confidence)."""
from __future__ import annotations

import re
import urllib.parse

from corp_osint_cli.http import fetch_json
from corp_osint_cli.schema import norm_domain

_DOMAIN_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$",
    re.I,
)


def _name_score(query: str, candidate: str) -> int:
    q = re.sub(r"[^a-z0-9]+", "", (query or "").lower())
    c = re.sub(r"[^a-z0-9]+", "", (candidate or "").lower())
    if not q or not c:
        return 0
    if q == c:
        return 100
    if q in c or c in q:
        return 70
    return 0


def clearbit_suggest(name: str) -> tuple[str, str]:
    """Return (domain, evidence_url) or ('', '')."""
    query = (name or "").strip()
    if not query:
        return "", ""
    url = (
        "https://autocomplete.clearbit.com/v1/companies/suggest?"
        + urllib.parse.urlencode({"query": query})
    )
    try:
        data = fetch_json(url, timeout=20)
    except Exception:
        return "", ""
    if not isinstance(data, list) or not data:
        return "", ""
    best_domain = ""
    best_score = 0
    for row in data[:5]:
        if not isinstance(row, dict):
            continue
        cname = str(row.get("name") or "").strip()
        domain = norm_domain(str(row.get("domain") or ""))
        if not domain or not _DOMAIN_RE.match(domain):
            continue
        score = _name_score(query, cname)
        if score > best_score:
            best_score = score
            best_domain = domain
    if best_score < 70 or not best_domain:
        return "", ""
    return best_domain, url


def duckduckgo_website(name: str) -> tuple[str, str]:
    """Return (domain, evidence_url) from DDG Instant Answer AbstractURL."""
    query = (name or "").strip()
    if not query:
        return "", ""
    api_url = (
        "https://api.duckduckgo.com/?"
        + urllib.parse.urlencode({"q": query, "format": "json", "no_redirect": "1"})
    )
    try:
        data = fetch_json(api_url, timeout=20)
    except Exception:
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    abstract_url = norm_domain(str(data.get("AbstractURL") or ""))
    if abstract_url and _DOMAIN_RE.match(abstract_url):
        return abstract_url, api_url
    for topic in data.get("RelatedTopics") or []:
        if not isinstance(topic, dict):
            continue
        first = norm_domain(str(topic.get("FirstURL") or ""))
        if first and _DOMAIN_RE.match(first):
            return first, api_url
    return "", ""


_LEGAL_SUFFIX = re.compile(
    r"\b(llc|inc|corp|ltd|gmbh|sa|nv|bv|pty|limited|holdings|company|co)\b",
    re.I,
)


def _brand_like(name: str) -> bool:
    core = _LEGAL_SUFFIX.sub("", name or "")
    core = re.sub(r"[^a-z0-9]+", "", core.lower())
    return 4 <= len(core) <= 32


def enrich_domain_hints(
    rows: list[dict[str, str]],
    *,
    max_lookups: int = 40,
) -> list[dict[str, str]]:
    """Fill empty domains using Clearbit then DDG; only when name match is strong.

    Capped so Exhibit 21 legal-shell lists do not fan out into hundreds of API calls.
    Parent and short brand-like names are tried first. Domain fill never invents a
    URL; original evidence_url / source provenance is kept unless it was empty.
    """
    out: list[dict[str, str]] = [dict(row) for row in rows]
    pending = [
        i
        for i, row in enumerate(out)
        if not row.get("domain")
        and (
            (row.get("relationship") or "") == "parent"
            or _brand_like(row.get("entity") or "")
        )
    ]
    pending.sort(key=lambda i: 0 if (out[i].get("relationship") or "") == "parent" else 1)
    lookups = 0
    for idx in pending:
        if lookups >= max_lookups:
            break
        copy = out[idx]
        entity = copy.get("entity") or ""
        domain, evidence = clearbit_suggest(entity)
        hint_source = "clearbit"
        if not domain:
            domain, evidence = duckduckgo_website(entity)
            hint_source = "duckduckgo"
        lookups += 1
        if not domain:
            continue
        copy["domain"] = domain
        if copy.get("source") in {"clearbit", "duckduckgo", "", "handoff"}:
            copy["source"] = hint_source
            copy["evidence_url"] = evidence
            copy["confidence"] = "medium"
        out[idx] = copy
    return out
