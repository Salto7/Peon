"""SEC EDGAR company resolve + Exhibit 21 subsidiaries."""

from __future__ import annotations

import html as html_lib
import json
import re
from html.parser import HTMLParser
from typing import Any

from ..http import AsyncHttpClient
from ..plugin import Plugin

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
ANNUAL = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}

_SKIP = re.compile(
    r"(?i)^("
    r"exhibit(\s+\d+)?|ex-\d+.*"
    r"|subsidiar(y|ies)(\s+of.*)?"
    r"|where incorporated|jurisdiction( of incorporation)?"
    r"|name of( the)? subsidiar(y|ies)|subsidiary name|company name"
    r"|^name$|^names?$"
    r"|percent(age)?( of)? ownership|ownership interest"
    r"|the following is a list|omitting subsidiaries|significant subsidiary"
    r"|sequence|filename|description|type|text|.*-ex21.*\.htm"
    r")$"
)
_ENTITY = re.compile(
    r"(?i)\b(inc\.?|corp\.?|corporation|llc|ltd\.?|limited|plc|gmbh|"
    r"s\.?\s?a\.?|s\.?\s?l\.?|c\.?\s?a\.?|b\.?\s?v\.?|n\.?\s?v\.?|"
    r"unlimited company|company|holdings|group|partners|lp|llp|"
    r"private limited|pte\.?|pty\.?)\b"
)
_JURISDICTION_ONLY = re.compile(
    r"(?i)^(delaware|california|nevada|new york|texas|florida|united states|"
    r"usa|canada|ontario|quebec|ireland|england|wales|germany|france|spain|"
    r"netherlands|luxembourg|singapore|hong kong|japan|china|mexico|brazil|"
    r"india|australia|switzerland|sweden|italy|belgium)$"
)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip = True
        if tag in {"br", "p", "tr", "div", "li", "td"}:
            self._chunks.append("\n" if tag != "td" else "\t")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._chunks.append(data)

    def text(self) -> str:
        return "".join(self._chunks)


class EdgarLookup(Plugin):
    name = "edgar"

    def __init__(self, http: AsyncHttpClient, config=None, **kwargs) -> None:
        super().__init__(http, config=config, **kwargs)
        self._tickers: list[dict[str, Any]] | None = None

    async def _load_tickers(self) -> list[dict[str, Any]]:
        if self._tickers is None:
            data = await self.http.get_json(TICKERS_URL)
            self._tickers = list(data.values()) if isinstance(data, dict) else data
        return self._tickers

    @staticmethod
    def cik10(cik: str | int) -> str:
        return str(cik).strip().lstrip("0").zfill(10) if str(cik).strip().lstrip("0") else "0".zfill(10)

    @staticmethod
    def _score(query: str, title: str) -> float:
        q = re.sub(r"[^a-z0-9\s]", " ", query.lower())
        t = re.sub(r"[^a-z0-9\s]", " ", title.lower())
        stop = {"inc", "corp", "corporation", "ltd", "llc", "the", "co", "company"}
        qw = [w for w in q.split() if w not in stop] or q.split()
        if not qw:
            return 0.0
        hits = sum(1 for w in qw if w in t)
        return (2.0 if q.strip() == t.strip() else 0.0) + hits / len(qw)

    async def resolve(
        self,
        company: str | None = None,
        ticker: str | None = None,
        cik: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a company using its name, stock market ticker symbol, or CIK."""
        if cik:
            c10 = self.cik10(cik)
            subs = await self.http.get_json(SUBMISSIONS_URL.format(cik10=c10))
            return {
                "cik": c10,
                "ticker": (subs.get("tickers") or [None])[0],
                "title": subs.get("name"),
                "legal_name": subs.get("name"),
                "sic": subs.get("sic"),
                "submissions": subs,
            }
        rows = await self._load_tickers()
        if ticker:
            t = ticker.upper().strip()
            for row in rows:
                if str(row.get("ticker", "")).upper() == t:
                    return await self.resolve(cik=str(row["cik_str"]))
            raise ValueError(f"Stock market ticker symbol not found: {ticker}")
        if not company:
            raise ValueError("company, stock market ticker symbol, or CIK required")
        ranked = sorted(rows, key=lambda r: self._score(company, str(r.get("title", ""))), reverse=True)
        best = ranked[0]
        if self._score(company, str(best.get("title", ""))) < 0.5:
            return {
                "cik": None,
                "match": "ambiguous",
                "candidates": [
                    {
                        "cik": self.cik10(r["cik_str"]),
                        "ticker": r.get("ticker"),
                        "title": r.get("title"),
                        "score": round(self._score(company, str(r.get("title", ""))), 3),
                    }
                    for r in ranked[:8]
                ],
                "submissions": None,
            }
        return await self.resolve(cik=str(best["cik_str"]))

    def _parse_names(self, html: str) -> list[str]:
        m = re.search(r"(?is)<html\b.*</html>", html)
        html = m.group(0) if m else html
        rows = re.findall(r"(?is)<tr\b[^>]*>(.*?)</tr>", html)
        candidates: list[str] = []
        for row in rows:
            tds = re.findall(r"(?is)<t[dh]\b[^>]*>(.*?)</t[dh]>", row)
            if not tds:
                continue
            first = re.sub(r"(?is)<[^>]+>", " ", tds[0])
            first = html_lib.unescape(first)
            first = re.sub(r"\s+", " ", first).replace("\xa0", " ").strip().strip(";\t")
            if first:
                candidates.append(first)
        names: list[str] = []
        seen: set[str] = set()
        for cell in candidates:
            cell = html_lib.unescape(cell).strip()
            if len(cell) < 3 or len(cell) > 160 or _SKIP.match(cell):
                continue
            if _JURISDICTION_ONLY.match(cell):
                continue
            # Require corporate entity cue OR numbered + Inc/Corp style already in _ENTITY
            if not _ENTITY.search(cell):
                # Allow "2117974 Ontario Inc." style via _ENTITY; reject bare headers
                continue
            # Reject column headers that slipped through with entity-like words
            low = cell.lower()
            if "name of" in low and "subsidiar" in low:
                continue
            key = low
            if key not in seen:
                seen.add(key)
                names.append(cell)
        return names

    async def extract_subsidiaries(self, info: dict[str, Any]) -> dict[str, Any]:
        subs = info.get("submissions")
        if not subs:
            return {
                "parent": {
                    "cik": info.get("cik"),
                    "ticker": info.get("ticker"),
                    "name": info.get("title"),
                    "match": info.get("match"),
                    "candidates": info.get("candidates"),
                },
                "subsidiaries": [],
                "errors": ["No SEC submissions resolved"],
            }
        recent = subs.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        dates = recent.get("filingDate", [])
        cik_num = str(subs.get("cik", "")).lstrip("0")
        subsidiaries: list[dict[str, Any]] = []
        files: list[dict[str, str]] = []
        errors: list[str] = []

        for i, form in enumerate(forms[:20]):
            if form not in ANNUAL or len(files) >= 3:
                continue
            acc = accessions[i]
            index_url = f"{ARCHIVES}/{cik_num}/{acc.replace('-', '')}/index.json"
            status, body, _ = await self.http.get(index_url)
            if status != 200:
                continue
            try:
                index = json.loads(body.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            for item in index.get("directory", {}).get("item", []):
                name = str(item.get("name", ""))
                upper = name.upper()
                if not any(x in upper for x in ("EX21", "EX-21", "SUBSIDIAR")):
                    continue
                if not re.search(r"\.(htm|html|txt)$", name, re.I):
                    continue
                url = f"{ARCHIVES}/{cik_num}/{acc.replace('-', '')}/{name}"
                files.append(
                    {
                        "form": form,
                        "filingDate": dates[i] if i < len(dates) else "",
                        "accessionNumber": acc,
                        "filename": name,
                        "url": url,
                    }
                )
                st, content = await self.http.get_text(url)
                if st != 200:
                    errors.append(f"HTTP {st} {url}")
                    break
                for n in self._parse_names(content):
                    subsidiaries.append(
                        {
                            "name": n,
                            "type": "subsidiary",
                            "confidence": "high",
                            "relationship_verified": True,
                            "evidence": f"Exhibit 21 from {form} filed {dates[i] if i < len(dates) else ''} ({acc})",
                            "sources": [url],
                        }
                    )
                break
            if subsidiaries:
                break

        parent_name = info.get("legal_name") or info.get("title")
        dedup = {s["name"].lower(): s for s in subsidiaries}
        return {
            "parent": {
                "cik": info.get("cik"),
                "ticker": info.get("ticker"),
                "name": parent_name,
                "legal_name": parent_name,
                "sic": info.get("sic"),
                "sources": [
                    SUBMISSIONS_URL.format(cik10=info["cik"]),
                    "https://www.sec.gov/edgar/search/",
                ],
            },
            "exhibit21_files": files,
            "subsidiaries": list(dedup.values()),
            "errors": errors,
        }

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        company = kwargs.get("company")
        ticker = kwargs.get("ticker")
        cik = kwargs.get("cik")
        self.log.info(
            "progress [edgar] resolve company=%r ticker=%s cik=%s",
            company,
            ticker,
            cik,
        )
        info = await self.resolve(company=company, ticker=ticker, cik=cik)
        self.log.info(
            "progress [edgar] extracting subsidiaries cik=%s",
            (info or {}).get("cik"),
        )
        return await self.extract_subsidiaries(info)
