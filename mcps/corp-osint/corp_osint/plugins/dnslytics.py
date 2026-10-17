"""DNSLytics search (search.dnslytics.com): extract GTM tags + reverse lookup domains.

Uses:
1) Playwright homepage GTM extraction (preferred — JS-rendered tags)
2) DNSLytics Domains dataset via Playwright in-page navigation (bot-protected API)
3) Optional official API if DNSLYTICS_API_KEY is set

All DNSLytics UI calls retry on HTTP 429 with backoff + page recycle.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any
from urllib.parse import quote

from ..http import (
    RATE_LIMIT_MAX_SEC,
    RATE_LIMIT_STEP_SEC,
    AsyncHttpClient,
    incremental_sleep,
    parse_retry_after,
)
from ..plugin import BrowserSupport, Plugin

GTM_RE = re.compile(r"\b(GTM-[A-Z0-9]{4,})\b", re.I)
TAG_RE = re.compile(r"\b((?:GTM|AW|DC|GT)-[A-Z0-9]{4,}|UA-\d{4,}-\d+)\b", re.I)
DOMAIN_IN_HIT = re.compile(
    r"\b([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+)\b",
    re.I,
)
SEARCH_UI = "https://search.dnslytics.com/search"
API_REVERSE = "https://api.dnslytics.net/v1/reverseganalytics/{tag}?apikey={key}"


class DnslyticsGtmLookup(Plugin, BrowserSupport):
    name = "dnslytics_gtm"

    def __init__(
        self,
        http: AsyncHttpClient,
        config=None,
        api_key: str | None = None,
        min_interval_sec: float = RATE_LIMIT_MAX_SEC,
        max_attempts: int = 5,
        **kwargs,
    ) -> None:
        Plugin.__init__(self, http, config=config, **kwargs)
        BrowserSupport.__init__(self)
        self.api_key = api_key or os.environ.get("DNSLYTICS_API_KEY")
        self.min_interval_sec = min(RATE_LIMIT_MAX_SEC, float(min_interval_sec))
        self.max_attempts = max_attempts
        self._lock = asyncio.Lock()
        self._page = None

    async def _reset_page(self) -> None:
        if self._page:
            try:
                await self._page.close()
            except Exception:  # noqa: BLE001
                pass
            self._page = None

    async def _ensure_page(self):
        if self._page:
            return self._page
        ctx = await self.ensure_browser()
        self._page = await ctx.new_page()
        await self._page.goto(SEARCH_UI, wait_until="domcontentloaded", timeout=45000)
        await self._page.wait_for_timeout(1000)
        return self._page

    def _scrape_domains_from_html(self, html: str) -> list[str]:
        """Best-effort domain extraction when API JSON is rate-limited."""
        found: list[str] = []
        seen: set[str] = set()
        for m in DOMAIN_IN_HIT.findall(html or ""):
            d = m.lower().strip(".")
            if (
                "." not in d
                or d in seen
                or d.endswith((".png", ".jpg", ".css", ".js", ".svg"))
                or "dnslytics" in d
                or "google" in d
            ):
                continue
            seen.add(d)
            found.append(d)
            if len(found) >= 40:
                break
        return found

    async def _dnslytics_query(self, query: str) -> dict[str, Any]:
        """Navigate search UI and capture Domains API JSON (throttled + 429 retries)."""
        last_err: str | None = None
        for attempt in range(1, self.max_attempts + 1):
            async with self._lock:
                try:
                    if attempt >= 3:
                        await self._reset_page()
                    page = await self._ensure_page()
                    url = f"{SEARCH_UI}?q={quote(query)}&d=domains"
                    data: dict[str, Any] = {}
                    async with page.expect_response(
                        lambda r: "a.dnslytics.com/v1/search/domains" in r.url,
                        timeout=45000,
                    ) as pending:
                        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    resp = await pending.value
                    if resp.status == 200:
                        data = await resp.json()
                        await asyncio.sleep(self.min_interval_sec)
                        return data or {"nhits": 0, "hits": []}
                    if resp.status == 429:
                        hdrs = {k.lower(): v for k, v in resp.headers.items()}
                        ra = parse_retry_after(hdrs.get("retry-after"))
                        wait = (
                            ra
                            if ra is not None
                            else RATE_LIMIT_STEP_SEC * attempt
                        )
                        wait = min(RATE_LIMIT_MAX_SEC, max(RATE_LIMIT_STEP_SEC, wait))
                        last_err = f"dnslytics rate limited (429) attempt={attempt}"
                        self.log.warning(
                            "dnslytics 429 query=%r attempt=%d sleep=%.2fs",
                            query,
                            attempt,
                            wait,
                        )
                        self.http.mark_cooldown("https://search.dnslytics.com/", wait)
                        await incremental_sleep(
                            wait,
                            reason=f"dnslytics-429:{query[:40]}",
                            logger=self.log,
                        )
                        await self._reset_page()
                        continue
                    last_err = f"dnslytics HTTP {resp.status}"
                    # Non-429 error: try HTML scrape once
                    html = await page.content()
                    tags = sorted({m.upper() for m in GTM_RE.findall(html)})
                    scraped = self._scrape_domains_from_html(html)
                    await asyncio.sleep(self.min_interval_sec)
                    hits = [{"name": d} for d in scraped]
                    if tags and not hits:
                        hits = [{"name": query.split(":")[-1], "tags": tags}]
                    return {
                        "nhits": len(hits),
                        "hits": hits,
                        "error": last_err,
                        "_fallback_tags": tags,
                    }
                except Exception as e:  # noqa: BLE001
                    last_err = str(e)
                    wait = min(RATE_LIMIT_MAX_SEC, RATE_LIMIT_STEP_SEC * attempt)
                    try:
                        page = self._page
                        html = await page.content() if page else ""
                    except Exception:  # noqa: BLE001
                        html = ""
                    tags = sorted({m.upper() for m in GTM_RE.findall(html)})
                    scraped = self._scrape_domains_from_html(html)
                    if scraped or tags:
                        await asyncio.sleep(self.min_interval_sec)
                        return {
                            "nhits": len(scraped),
                            "hits": [{"name": d} for d in scraped]
                            or ([{"name": query.split(":")[-1], "tags": tags}] if tags else []),
                            "error": last_err,
                            "_fallback_tags": tags,
                        }
                    await self._reset_page()
                    await incremental_sleep(
                        wait,
                        reason=f"dnslytics-error:{query[:40]}",
                        logger=self.log,
                    )

        return {
            "nhits": 0,
            "hits": [],
            "error": last_err or "dnslytics failed after retries",
        }

    async def extract_tags_from_site(self, domain: str) -> list[str]:
        """Prefer Playwright (JS-rendered GTM); fall back to aiohttp HTML."""
        tags: set[str] = set()
        # Playwright first — many sites inject GTM via JS
        try:
            ctx = await self.ensure_browser()
            page = await ctx.new_page()
            try:
                for url in (f"https://www.{domain}/", f"https://{domain}/"):
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                        await page.wait_for_timeout(2500)
                        html = await page.content()
                        for m in GTM_RE.findall(html):
                            tags.add(m.upper())
                        # Also check network/script URLs in page content
                        for pat in (
                            r"gtm\.js\?id=(GTM-[A-Z0-9]+)",
                            r"googletagmanager\.com/ns\.html\?id=(GTM-[A-Z0-9]+)",
                        ):
                            for m in re.findall(pat, html, re.I):
                                tags.add(m.upper())
                        if tags:
                            break
                    except Exception:  # noqa: BLE001
                        continue
            finally:
                await page.close()
        except Exception:  # noqa: BLE001
            pass

        if tags:
            return sorted(tags)

        for url in (f"https://www.{domain}/", f"https://{domain}/", f"http://{domain}/"):
            try:
                status, body = await self.http.get_text(url, timeout=15.0)
            except Exception:  # noqa: BLE001
                continue
            if status >= 400:
                continue
            for pat in (
                r"\b(GTM-[A-Z0-9]{4,})\b",
                r"gtm\.js\?id=(GTM-[A-Z0-9]+)",
                r"googletagmanager\.com/ns\.html\?id=(GTM-[A-Z0-9]+)",
                r"['\"]gtmId['\"]\s*:\s*['\"](GTM-[A-Z0-9]+)['\"]",
            ):
                for m in re.findall(pat, body, re.I):
                    tags.add(m.upper())
            if tags:
                break
        return sorted(tags)

    def _tags_from_hit(self, hit: dict[str, Any]) -> list[str]:
        blob = json.dumps(hit)
        return sorted({m.upper() for m in GTM_RE.findall(blob)})

    async def lookup_domain_tags(self, domain: str) -> dict[str, Any]:
        domain = domain.lower().strip().removeprefix("www.")
        site_tags = await self.extract_tags_from_site(domain)
        dns_tags: list[str] = []
        hit_name = None
        err = None
        try:
            data = await self._dnslytics_query(f"fqdn:{domain}")
            if data.get("error") and not data.get("hits"):
                err = data.get("error")
            for hit in data.get("hits") or []:
                if str(hit.get("name", "")).lower() == domain or domain.endswith(
                    str(hit.get("name", "")).lower()
                ):
                    hit_name = hit.get("name")
                    dns_tags = self._tags_from_hit(hit)
                    break
            if not dns_tags and data.get("hits"):
                dns_tags = self._tags_from_hit(data["hits"][0])
                hit_name = data["hits"][0].get("name")
            for t in data.get("_fallback_tags") or []:
                if t not in dns_tags:
                    dns_tags.append(t)
            if data.get("error") and not dns_tags:
                err = data.get("error")
        except Exception as e:  # noqa: BLE001
            err = str(e)

        tags = sorted(set(site_tags) | set(dns_tags))
        return {
            "domain": domain,
            "tags": tags,
            "site_tags": site_tags,
            "dnslytics_tags": dns_tags,
            "dnslytics_hit": hit_name,
            "sources": [
                f"{SEARCH_UI}?q={quote('fqdn:' + domain)}&d=domains",
                f"https://{domain}/",
            ],
            "error": err,
        }

    async def reverse_tag(self, tag: str, limit: int = 40) -> dict[str, Any]:
        tag = tag.upper().strip()
        domains: list[dict[str, Any]] = []
        err = None
        source = "dnslytics_search"

        if self.api_key:
            try:
                url = API_REVERSE.format(tag=quote(tag), key=quote(self.api_key))
                status, body, _ = await self.http.get(url, headers={"Accept": "application/json"})
                if status < 400:
                    doc = json.loads(body.decode("utf-8", errors="replace"))
                    data = doc.get("data") or doc
                    for d in (data.get("domains") or [])[:limit]:
                        name = d.get("domain") if isinstance(d, dict) else str(d)
                        if name:
                            domains.append(
                                {
                                    "domain": name.lower(),
                                    "firstseen": d.get("firstseen") if isinstance(d, dict) else None,
                                    "lastseen": d.get("lastseen") if isinstance(d, dict) else None,
                                }
                            )
                    source = "dnslytics_api"
                else:
                    err = f"API HTTP {status}"
            except Exception as e:  # noqa: BLE001
                err = f"API: {e}"

        if not domains:
            try:
                data = await self._dnslytics_query(f"html.tag:{tag}")
                for hit in (data.get("hits") or [])[:limit]:
                    name = hit.get("name")
                    if name:
                        domains.append({"domain": str(name).lower()})
                if data.get("error") and not domains:
                    err = data.get("error")
            except Exception as e:  # noqa: BLE001
                err = str(e)

        return {
            "tag": tag,
            "domains": domains,
            "count": len(domains),
            "source": source,
            "sources": [f"{SEARCH_UI}?q={quote('html.tag:' + tag)}&d=domains"],
            "error": err,
        }

    async def run(
        self,
        domains: list[str] | None = None,
        domain: str | None = None,
        max_domains: int = 5,
        max_gtm_reverse: int = 5,
        **kwargs: Any,
    ) -> dict[str, Any]:
        seeds = list(domains or [])
        if domain and domain not in seeds:
            seeds.insert(0, domain)
        seeds = seeds[:max_domains]

        tag_results = []
        all_tags: list[str] = []
        self.log.info("GTM lookup for %d seed domain(s)", len(seeds))
        for d in seeds:
            self.log.debug("extract tags for %s", d)
            tr = await self.lookup_domain_tags(d)
            tag_results.append(tr)
            self.log.info(
                "%s tags=%s error=%s",
                d,
                tr.get("tags") or [],
                tr.get("error"),
            )
            for t in tr.get("tags") or []:
                if t not in all_tags:
                    all_tags.append(t)

        reverse_results = []
        discovered: dict[str, dict[str, Any]] = {}
        for tag in all_tags[:max_gtm_reverse]:
            self.log.info("reverse GTM %s", tag)
            rev = await self.reverse_tag(tag)
            reverse_results.append(rev)
            for item in rev.get("domains") or []:
                d = item["domain"]
                entry = discovered.setdefault(
                    d,
                    {
                        "domain": d,
                        "relation": "gtm_reverse",
                        "gtm_tags": [],
                        "confidence": "medium",
                        "evidence": "Shares GTM tag via DNSLytics reverse lookup",
                        "sources": list(rev.get("sources") or []),
                    },
                )
                if tag not in entry["gtm_tags"]:
                    entry["gtm_tags"].append(tag)

        return {
            "seed_domains": seeds,
            "tag_lookups": tag_results,
            "tags": all_tags,
            "reverse": reverse_results,
            "domains": list(discovered.values()),
            "errors": [
                x["error"]
                for x in tag_results + reverse_results
                if x.get("error")
            ],
        }

    async def close(self) -> None:
        await self._reset_page()
        await self.close_browser()
