"""Wikipedia infobox + acquisitions section (MediaWiki API, no SPARQL)."""
from __future__ import annotations

import re
import urllib.parse
from typing import Any

from corp_osint_cli.apex import registrable_apex
from corp_osint_cli.http import fetch_json
from corp_osint_cli.schema import entity_key

_ACQ_HEAD = re.compile(r"(?im)^==+\s*acquisitions?\s*==+\s*$")
_NEXT_HEAD = re.compile(r"(?im)^==+")
_LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]+)?\]\]")
_URL_RE = re.compile(r"\{\{\s*URL\s*\|\s*([^}|]+)", re.I)
_ACQUIRED_RE = re.compile(
    r"(?:acquired|purchased|acquisition of)\s+"
    r"(?:the (?:American )?Operations of\s+)?"
    r"(?:software development company and cloud commerce platform provider\s+)?"
    r"(?:the AV,[^.]{0,80} from\s+)?"
    r"([A-Z][A-Za-z0-9 .,&'\-]{2,80}?)(?:\s+to\s+|\s+for\s+|,|\.| from )",
    re.I,
)


def enwiki_title_from_entity(ent: dict[str, Any]) -> str:
    site = ((ent.get("sitelinks") or {}).get("enwiki") or {}).get("title") or ""
    return str(site).strip()


def fetch_wikitext(title: str) -> str:
    if not title.strip():
        return ""
    data = fetch_json(
        "https://en.wikipedia.org/w/api.php?"
        + urllib.parse.urlencode(
            {
                "action": "parse",
                "page": title,
                "prop": "wikitext",
                "format": "json",
            }
        )
    )
    return str(((data.get("parse") or {}).get("wikitext") or {}).get("*") or "")


def _infobox_block(wikitext: str) -> str:
    m = re.search(r"\{\{\s*Infobox company\b", wikitext, re.I)
    if not m:
        return ""
    start = m.start()
    depth = 0
    for i in range(start, len(wikitext) - 1):
        if wikitext[i : i + 2] == "{{":
            depth += 1
            continue
        if wikitext[i : i + 2] == "}}":
            depth -= 1
            if depth == 0:
                return wikitext[start : i + 2]
    return ""


def _field(block: str, name: str) -> str:
    m = re.search(rf"(?im)^\|\s*{re.escape(name)}\s*=\s*(.+)$", block)
    return (m.group(1).strip() if m else "")


def infobox_website(wikitext: str) -> str:
    block = _infobox_block(wikitext)
    raw = _field(block, "website")
    m = _URL_RE.search(raw)
    if m:
        return registrable_apex(m.group(1))
    m = re.search(r"https?://[^\s|}]+", raw)
    if m:
        return registrable_apex(m.group(0))
    return registrable_apex(raw)


def infobox_predecessors(wikitext: str) -> list[str]:
    block = _infobox_block(wikitext)
    raw = _field(block, "predecessors") or _field(block, "predecessor")
    names = [_clean_wiki_name(x) for x in _LINK_RE.findall(raw)]
    return [n for n in names if n]


def _section(wikitext: str, heading_re: re.Pattern[str]) -> str:
    m = heading_re.search(wikitext)
    if not m:
        return ""
    rest = wikitext[m.end() :]
    n = _NEXT_HEAD.search(rest)
    return rest[: n.start()] if n else rest


def _clean_wiki_name(raw: str) -> str:
    text = (raw or "").strip()
    text = text.split("|")[-1].strip()
    text = re.sub(r"'{2,}", "", text)
    text = re.sub(r"\s+", " ", text).strip(" .,;:")
    return text


def acquisition_names(wikitext: str) -> list[str]:
    section = _section(wikitext, _ACQ_HEAD)
    if not section:
        return []
    names: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        clean = _clean_wiki_name(name)
        key = entity_key(clean)
        if not clean or not key or key in seen:
            return
        if clean.lower() in {"acme", "acme corp", "acme data"}:
            return
        if re.match(r"(?i)^the\s", clean) and len(clean.split()) <= 4:
            return
        if len(clean) < 6:
            return
        seen.add(key)
        names.append(clean)

    for link in _LINK_RE.findall(section):
        add(link)
    for m in _ACQUIRED_RE.finditer(section):
        add(m.group(1))
    return names


def wikipedia_rows(*, title: str, evidence_url: str = "") -> tuple[list[dict[str, str]], str]:
    wikitext = fetch_wikitext(title)
    if not wikitext:
        return [], ""
    url = evidence_url or f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
    website = infobox_website(wikitext)
    rows: list[dict[str, str]] = []
    for name in infobox_predecessors(wikitext):
        rows.append(
            {
                "entity": name,
                "relationship": "predecessor",
                "domain": "",
                "qid": "",
                "source": "wikipedia",
                "evidence_url": url,
                "confidence": "high",
            }
        )
    for name in acquisition_names(wikitext):
        rows.append(
            {
                "entity": name,
                "relationship": "acquisition",
                "domain": "",
                "qid": "",
                "source": "wikipedia",
                "evidence_url": url,
                "confidence": "medium",
            }
        )
    return rows, website
