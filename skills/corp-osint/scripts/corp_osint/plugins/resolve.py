"""Lightweight domain candidate resolve + homepage verification."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any, Optional

from ..http import AsyncHttpClient
from ..parking import detect_parking
from ..plugin import Plugin


class _TitleDesc(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self.metas: list[str] = []
        self.text_bits: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        ad = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
        if tag in {"script", "style"}:
            self._skip = True
        if tag == "meta":
            name = (ad.get("name") or ad.get("property") or "").lower()
            if name in {"description", "og:description", "og:title", "og:site_name"}:
                self.metas.append(ad.get("content", ""))

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in {"script", "style"}:
            self._skip = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip and len("".join(self.text_bits)) < 3000:
            self.text_bits.append(data)


class DomainResolver(Plugin):
    name = "resolve_domains"

    def __init__(self, http: AsyncHttpClient, config=None, **kwargs) -> None:
        super().__init__(http, config=config, **kwargs)

    @staticmethod
    def candidates(name: str, parent_domain: Optional[str] = None) -> list[str]:
        """Guess domains only for brand-like names — never invent from legal shells."""
        low = name.lower().strip()
        # Do not invent domains from exhibit headers / numbered holding cos
        if any(
            x in low
            for x in (
                "name of the subsidiary",
                "name of subsidiary",
                "where incorporated",
            )
        ):
            return [parent_domain.lower()] if parent_domain else []
        if re.match(r"^\d{3,}", low):  # "2117974 Ontario Inc."
            return [parent_domain.lower()] if parent_domain else []

        base = re.sub(r"[^a-z0-9\s]", " ", low)
        base = re.sub(
            r"\b(incorporated|corporation|company|limited|inc|corp|llc|ltd|plc|gmbh|"
            r"the|co|private|holdings|holding|group|solutions|technologies|technology)\b",
            " ",
            base,
        )
        base = re.sub(r"\s+", " ", base).strip()
        words = [w for w in base.split() if len(w) > 1]
        # Need a real brand token — skip empty / too-generic
        if len(words) < 1 or (len(words) == 1 and len(words[0]) < 4):
            return [parent_domain.lower()] if parent_domain else []

        compact = "".join(words)
        out: list[str] = []
        # Prefer short brand slugs only (max ~2-3 tokens)
        for slug in (compact, "".join(words[:2]), words[0]):
            if not slug or len(slug) < 4 or len(slug) > 40:
                continue
            for tld in (".com", ".net"):
                d = f"{slug}{tld}"
                if d not in out:
                    out.append(d)
        if parent_domain and parent_domain.lower() not in out:
            out.append(parent_domain.lower())
        return out[:5]

    async def run(
        self,
        name: str,
        parent_domain: Optional[str] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        results = []
        for domain in self.candidates(name, parent_domain):
            site = await self._fetch(domain)
            results.append(self._verify(name, site, parent_domain))
        results.sort(
            key=lambda r: (
                0 if r.get("relationship_verified") else 1,
                {"high": 0, "medium": 1, "low": 2}.get(r.get("confidence", "low"), 3),
            )
        )
        return {
            "name": name,
            "parent_domain": parent_domain,
            "domains": results,
            "recommended": next((r for r in results if r.get("relationship_verified")), None),
        }

    async def _fetch(self, domain: str) -> dict[str, Any]:
        for url in (f"https://{domain}/", f"http://{domain}/"):
            try:
                status, body = await self.http.get_text(url, timeout=12.0)
            except Exception as e:  # noqa: BLE001
                return {"domain": domain, "url": url, "ok": False, "error": str(e)}
            if status < 400:
                parser = _TitleDesc()
                try:
                    parser.feed(body)
                    parser.close()
                except Exception:  # noqa: BLE001
                    pass
                return {
                    "domain": domain,
                    "url": url,
                    "ok": True,
                    "status": status,
                    "title": parser.title.strip(),
                    "meta": [m for m in parser.metas if m],
                    "text_sample": re.sub(r"\s+", " ", " ".join(parser.text_bits))[:3000],
                    "html": body[:200000],
                }
        return {"domain": domain, "ok": False, "error": "unreachable"}

    @staticmethod
    def _verify(name: str, site: dict[str, Any], parent_domain: Optional[str]) -> dict[str, Any]:
        if not site.get("ok"):
            return {
                "domain": site.get("domain"),
                "relationship_verified": False,
                "confidence": "low",
                "evidence": site.get("error") or "unreachable",
                "sources": [],
            }
        stop = {"inc", "corp", "corporation", "llc", "ltd", "company", "the", "co", "group"}
        tokens = [t for t in re.findall(r"[a-z0-9]+", name.lower()) if t not in stop and len(t) > 2]
        blob = " ".join(
            [site.get("title") or "", " ".join(site.get("meta") or []), site.get("text_sample") or ""]
        ).lower()
        parked, marker = detect_parking(
            title=site.get("title") or "",
            text=site.get("text_sample") or "",
            html=site.get("html") or "",
            final_url=site.get("url") or "",
        )
        if parked:
            # Keep parked domains — later Entra match ⇒ dangling / subdomain takeover risk
            return {
                "domain": site["domain"],
                "relationship_verified": False,
                "confidence": "low",
                "evidence": f"Parking / for-sale page ({marker}) — kept for Entra dangling check",
                "sources": [site["url"]],
                "is_parked": True,
                "parking_marker": marker,
                "title": site.get("title"),
            }
        hits = [t for t in tokens if t in blob]
        ratio = len(hits) / max(len(tokens), 1)
        verified = ratio >= 0.6 and bool(hits)
        conf = "high" if ratio >= 0.8 else ("medium" if verified else "low")
        evidence = f"Name tokens {hits} cover {ratio:.0%}" if hits else "No strong name match"
        if parent_domain and parent_domain.lower() in blob:
            verified = True
            conf = "high"
            evidence += f"; mentions parent {parent_domain}"
        return {
            "domain": site["domain"],
            "relationship_verified": verified,
            "confidence": conf,
            "evidence": evidence,
            "sources": [site["url"]],
            "title": site.get("title"),
            "html": site.get("html"),
            "is_parked": False,
        }
