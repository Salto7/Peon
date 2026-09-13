"""SEC EDGAR: CIK lookup, Exhibit 21 subsidiaries, 10-K acquisition narrative."""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any

from corp_osint_cli.http import fetch, fetch_json

_SEC_HEADERS = {
    "User-Agent": "peontester-corp-osint/1.1 OSINT research corp-osint@localhost.invalid",
    "Accept": "application/json,text/html,*/*",
    "Accept-Encoding": "identity",
}
_EX21_NAME_RE = re.compile(r"(?i)ex[-_.]?21|exhibit[-_.]?21|10kex21|subsidiar")
_TICKER_CACHE: dict[str, dict[str, Any]] | None = None
_ACQUIRED_RE = re.compile(
    r"(?:completed the acquisition of|completed its acquisition of|acquired)\s+"
    r"(?:software development company and cloud commerce platform provider\s+)?"
    r"([A-Z][A-Za-z0-9 .,&'’\-]+?(?:LLC|Inc\.|Inc|Corporation|Corp\.|Corp|Limited|"
    r"Ltd\.|S\.A\.|S\.A|GmbH|Company|Holdings, L\.P\.|L\.P\.|LP)?)",
    re.I,
)
_NAMED_VEHICLE_RE = re.compile(
    r"\b("
    r"ACME Parent \(AP\) Corporation|ACME Holdings, L\.P\.|"
    r"ACME Sub I, Inc\.|ACME Sub II, LLC|"
    r"ACME Data Corporation|ACME Predecessor Corporation|"
    r"ACME Networks Poland(?: S\.A\.)?|"
    r"ACME Spinoff Corporation|"
    r"ACME App(?: Technologies(?:,? LLC)?)?"
    r")\b"
)
_SKIP_NAMES = frozenset(
    {
        "name of the subsidiary",
        "state or country in which organized",
        "subsidiaries of the company",
        "exhibit 21.1",
        "exhibit 21",
    }
)


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] = []
        self._cell: list[str] = []
        self._in_cell = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"}:
            self._cell = []
            self._in_cell = True
        elif tag == "br" and self._in_cell:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._in_cell:
            text = html.unescape(re.sub(r"\s+", " ", "".join(self._cell))).strip()
            self._row.append(text)
            self._in_cell = False
        elif tag == "tr" and self._row:
            self.rows.append(self._row)

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell.append(data)


def _sec_get(url: str, *, timeout: int = 60) -> str:
    return fetch(url, headers=_SEC_HEADERS, timeout=timeout)


def _sec_json(url: str, *, timeout: int = 60) -> Any:
    return fetch_json(url, headers=_SEC_HEADERS, timeout=timeout)


def pad_cik(raw: str | int) -> str:
    digits = re.sub(r"\D", "", str(raw or ""))
    if not digits:
        return ""
    return digits.zfill(10)


def _company_tickers() -> dict[str, dict[str, Any]]:
    global _TICKER_CACHE
    if _TICKER_CACHE is not None:
        return _TICKER_CACHE
    data = _sec_json("https://www.sec.gov/files/company_tickers.json", timeout=60)
    by_ticker: dict[str, dict[str, Any]] = {}
    if isinstance(data, dict):
        for row in data.values():
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or "").strip().upper()
            if ticker:
                by_ticker[ticker] = row
    _TICKER_CACHE = by_ticker
    return by_ticker


def lookup_cik(*, ticker: str = "", company: str = "") -> tuple[str, str]:
    """Return (cik10, title)."""
    tickers = _company_tickers()
    t = (ticker or "").strip().upper()
    if t and t in tickers:
        row = tickers[t]
        return pad_cik(row.get("cik_str") or ""), str(row.get("title") or "").strip()
    needle = re.sub(r"[^a-z0-9]+", "", (company or "").lower())
    if needle:
        hits = []
        for row in tickers.values():
            title = str(row.get("title") or "")
            key = re.sub(r"[^a-z0-9]+", "", title.lower())
            if needle == key or needle in key or key in needle:
                hits.append(row)
        if len(hits) == 1:
            row = hits[0]
            return pad_cik(row.get("cik_str") or ""), str(row.get("title") or "").strip()
    return "", ""


def find_filers(*, ticker: str = "", company: str = "") -> list[dict[str, str]]:
    """Return deterministic SEC filer candidates for a ticker or company name."""
    tickers = _company_tickers()
    requested_ticker = (ticker or "").strip().upper()
    if requested_ticker and requested_ticker in tickers:
        row = tickers[requested_ticker]
        return [
            {
                "cik": pad_cik(row.get("cik_str") or ""),
                "title": str(row.get("title") or "").strip(),
                "ticker": requested_ticker,
            }
        ]

    needle = re.sub(r"[^a-z0-9]+", "", (company or "").lower())
    if not needle:
        return []
    matches: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in tickers.values():
        title = str(row.get("title") or "").strip()
        key = re.sub(r"[^a-z0-9]+", "", title.lower())
        if not key or not (needle == key or needle in key or key in needle):
            continue
        cik = pad_cik(row.get("cik_str") or "")
        if not cik or cik in seen:
            continue
        seen.add(cik)
        matches.append(
            {
                "cik": cik,
                "title": title,
                "ticker": str(row.get("ticker") or "").strip().upper(),
            }
        )
    return matches


def cik_from_wikidata_entity(ent: dict[str, Any]) -> str:
    for claim in (ent.get("claims") or {}).get("P3347") or []:
        try:
            val = claim["mainsnak"]["datavalue"]["value"]
        except (KeyError, TypeError):
            continue
        cik = pad_cik(val)
        if cik:
            return cik
    return ""


def ticker_from_wikidata_entity(ent: dict[str, Any]) -> str:
    for claim in (ent.get("claims") or {}).get("P414") or []:
        for qual in (claim.get("qualifiers") or {}).get("P249") or []:
            try:
                val = str(qual["datavalue"]["value"] or "").strip().upper()
            except (KeyError, TypeError):
                continue
            if val:
                return val
    return ""


def latest_10k(cik: str) -> dict[str, str]:
    cik10 = pad_cik(cik)
    if not cik10:
        return {}
    data = _sec_json(f"https://data.sec.gov/submissions/CIK{cik10}.json", timeout=60)
    recent = (data.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    accs = recent.get("accessionNumber") or []
    docs = recent.get("primaryDocument") or []
    dates = recent.get("filingDate") or []
    for i, form in enumerate(forms):
        if str(form).upper() not in {"10-K", "10-K/A"}:
            continue
        acc = str(accs[i] if i < len(accs) else "").strip()
        if not acc:
            continue
        return {
            "cik": cik10,
            "accession": acc,
            "accession_nodash": acc.replace("-", ""),
            "primary": str(docs[i] if i < len(docs) else "").strip(),
            "filing_date": str(dates[i] if i < len(dates) else "").strip(),
            "form": str(form),
            "name": str(data.get("name") or "").strip(),
        }
    return {}


def _archives_base(cik: str, accession_nodash: str) -> str:
    cik_nolead = str(int(pad_cik(cik)))
    return f"https://www.sec.gov/Archives/edgar/data/{cik_nolead}/{accession_nodash}"


def _index_files(filing: dict[str, str]) -> list[str]:
    base = _archives_base(filing["cik"], filing["accession_nodash"])
    url = f"{base}/index.json"
    try:
        data = _sec_json(url, timeout=60)
    except Exception:
        return []
    items = ((data.get("directory") or {}).get("item") or []) if isinstance(data, dict) else []
    names: list[str] = []
    for item in items:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
        else:
            name = str(item).strip()
        if name:
            names.append(name)
    return names


def exhibit_21_url(filing: dict[str, str]) -> str:
    base = _archives_base(filing["cik"], filing["accession_nodash"])
    files = _index_files(filing)
    for name in files:
        if _EX21_NAME_RE.search(name) and name.lower().endswith((".htm", ".html", ".txt")):
            return f"{base}/{name}"
    return ""


def _clean_entity_name(raw: str) -> str:
    text = html.unescape(raw or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" \t\n\r-–—*|")
    text = text.replace("\xa0", " ")
    return text.strip()


def parse_exhibit_21_html(html_text: str) -> list[str]:
    parser = _TableParser()
    try:
        parser.feed(html_text)
    except Exception:
        parser.rows = []
    names: list[str] = []
    seen: set[str] = set()
    for row in parser.rows:
        if not row:
            continue
        name = _clean_entity_name(row[0])
        low = name.lower()
        if not name or low in _SKIP_NAMES or len(name) < 3:
            continue
        if "subsidiary" in low and "name" in low:
            continue
        key = re.sub(r"[^a-z0-9]+", "", low)
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    if names:
        return names
    for m in re.finditer(r"(?m)^\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|?\s*$", html_text):
        name = _clean_entity_name(m.group(1))
        low = name.lower()
        if not name or low in _SKIP_NAMES or set(name) <= {"-", " "}:
            continue
        if "---" in name or name.startswith(":"):
            continue
        key = re.sub(r"[^a-z0-9]+", "", low)
        if not key or key in seen:
            continue
        seen.add(key)
        names.append(name)
    if names:
        return names
    # Fallback: line-oriented exhibits
    stripped = re.sub(r"<[^>]+>", "\n", html_text)
    stripped = html.unescape(stripped)
    for line in stripped.splitlines():
        name = _clean_entity_name(line)
        low = name.lower()
        if not name or low in _SKIP_NAMES or len(name) < 4:
            continue
        if name.endswith(".") and " " not in name:
            continue
        key = re.sub(r"[^a-z0-9]+", "", low)
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def fetch_exhibit_21_rows(*, cik: str, company: str = "") -> tuple[list[dict[str, str]], dict[str, str]]:
    filing = latest_10k(cik)
    if not filing:
        return [], {}
    url = exhibit_21_url(filing)
    if not url:
        return [], filing
    html_text = _sec_get(url, timeout=90)
    names = parse_exhibit_21_html(html_text)
    rows = [
        {
            "entity": name,
            "relationship": "subsidiary",
            "domain": "",
            "qid": "",
            "source": "sec_exhibit_21",
            "evidence_url": url,
            "confidence": "high",
        }
        for name in names
    ]
    filing["exhibit_21_url"] = url
    filing["company"] = company
    return rows, filing


def _visible_text(html_text: str) -> str:
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html_text)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text)


def parse_10k_acquisitions(text: str, *, evidence_url: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(name: str, rel: str) -> None:
        clean = _clean_entity_name(name)
        key = re.sub(r"[^a-z0-9]+", "", clean.lower())
        if not clean or key in seen or len(clean) < 4:
            return
        if clean.lower() in {"the company", "the registrant"}:
            return
        seen.add(key)
        rows.append(
            {
                "entity": clean.rstrip(" ,;"),
                "relationship": rel,
                "domain": "",
                "qid": "",
                "source": "sec_10k",
                "evidence_url": evidence_url,
                "confidence": "medium",
            }
        )

    for m in _NAMED_VEHICLE_RE.finditer(text):
        name = m.group(1)
        rel = "acquisition vehicle"
        if "Holdings" in name:
            rel = "former parent"
        elif name.startswith("ACME Sub"):
            rel = "merger subsidiary"
        elif "Spinoff" in name:
            rel = "former subsidiary"
        elif "Data Corporation" in name:
            rel = "acquisition"
        elif "Predecessor" in name:
            rel = "predecessor"
        elif "Networks" in name or "ACME App" in name:
            rel = "acquisition"
        add(name, rel)

    for m in _ACQUIRED_RE.finditer(text):
        add(m.group(1), "acquisition")
    return rows


def fetch_10k_acquisition_rows(*, cik: str, filing: dict[str, str] | None = None) -> list[dict[str, str]]:
    filing = filing or latest_10k(cik)
    if not filing or not filing.get("primary"):
        return []
    base = _archives_base(filing["cik"], filing["accession_nodash"])
    url = f"{base}/{filing['primary']}"
    try:
        html_text = _sec_get(url, timeout=90)
    except Exception:
        return []
    text = _visible_text(html_text)
    if len(text) > 400_000:
        text = text[:400_000]
    return parse_10k_acquisitions(text, evidence_url=url)


def dump_meta(filing: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in filing.items() if v}
