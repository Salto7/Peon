"""Playwright web search + homepage link discovery for subsidiaries/brands/domains."""

from __future__ import annotations

import asyncio
import base64
import re
from typing import Any, Optional
from urllib.parse import parse_qs, quote_plus, urlparse

from ..plugin import BrowserSupport, Plugin
from ..http import AsyncHttpClient, incremental_sleep

_STOP = re.compile(
    r"(?i)(^|\.)(duckduckgo\.|google\.|bing\.|yahoo\.|wikipedia\.|facebook\.|"
    r"linkedin\.|youtube\.|sec\.gov|microsoftonline\.|twitter\.|instagram\.|"
    r"apple\.com|play\.google)"
)


def _apex(host: str) -> str:
    host = host.lower().strip().removeprefix("www.")
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _unwrap_bing(href: str) -> str:
    if "bing.com/ck/" not in href:
        return href
    qs = parse_qs(urlparse(href).query)
    u = (qs.get("u") or [None])[0]
    if not u:
        return href
    raw = u.removeprefix("a1")
    pad = "=" * (-len(raw) % 4)
    try:
        decoded = base64.urlsafe_b64decode(raw + pad).decode("utf-8", errors="replace")
        return decoded if decoded.startswith("http") else href
    except Exception:  # noqa: BLE001
        return href


class PlaywrightWebSearch(Plugin, BrowserSupport):
    name = "playwright_search"

    def __init__(self, http: AsyncHttpClient, config=None, **kwargs) -> None:
        Plugin.__init__(self, http, config=config, **kwargs)
        BrowserSupport.__init__(self)

    def _queries(self, company: str, domain: Optional[str], max_queries: int) -> list[str]:
        # When no domain is known, prioritize finding the official site first.
        if domain:
            queries = [
                f'"{company}" subsidiaries',
                f'"{company}" "wholly owned subsidiary"',
                f'"{company}" brands OR "our brands"',
                f'"{company}" official website',
                f'"{company}" "acquired" OR "acquisition"',
                f"site:{domain} (brands OR subsidiaries OR companies)",
                f'"{domain}" subsidiaries',
            ]
        else:
            queries = [
                f'"{company}" official website',
                f'"{company}" official site',
                f'"{company}" subsidiaries',
                f'"{company}" brands OR "our brands"',
                f'"{company}" "acquired" OR "acquisition"',
            ]
        # Dedup preserve order
        seen: set[str] = set()
        out: list[str] = []
        for q in queries:
            if q not in seen:
                seen.add(q)
                out.append(q)
        return out[:max_queries]

    async def _search_bing(self, page, q: str, attempts: int = 3) -> list[dict[str, str]]:
        last_err: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                await page.goto(
                    f"https://www.bing.com/search?q={quote_plus(q)}&setlang=en-US&cc=US",
                    wait_until="domcontentloaded",
                    timeout=45000,
                )
                await page.wait_for_timeout(1000 + 400 * (attempt - 1))
                # Soft rate-limit / captcha signals
                content = (await page.content()).lower()
                if "too many requests" in content or "unusual traffic" in content:
                    await incremental_sleep(
                        0.2 * attempt,
                        reason="bing-rate-limit",
                        logger=self.log,
                    )
                    continue
                links = await page.eval_on_selector_all(
                    "li.b_algo h2 a",
                    "els => els.map(e => ({href: e.href, title: (e.textContent || '').trim()}))",
                )
                if links:
                    return links
                # Retry if empty (sometimes bot interstitial)
                await incremental_sleep(
                    0.2 * attempt,
                    reason="bing-empty-retry",
                    logger=self.log,
                )
            except Exception as e:  # noqa: BLE001
                last_err = e
                await incremental_sleep(
                    0.2 * attempt,
                    reason="bing-error-retry",
                    logger=self.log,
                )
        if last_err:
            raise last_err
        return []

    async def _search_ddg(self, page, q: str) -> list[dict[str, str]]:
        await page.goto(
            f"https://html.duckduckgo.com/html/?q={quote_plus(q)}",
            wait_until="domcontentloaded",
            timeout=45000,
        )
        await page.wait_for_timeout(800)
        links = await page.eval_on_selector_all(
            "a.result__a",
            "els => els.map(e => ({href: e.href, title: (e.textContent || '').trim()}))",
        )
        return links or []

    async def _homepage_domains(self, page, domain: str, company: str) -> list[dict[str, Any]]:
        """Crawl primary homepage for outbound related domains (Playwright)."""
        found: dict[str, dict[str, Any]] = {}
        tokens = [
            t
            for t in re.findall(r"[a-z0-9]+", company.lower())
            if t not in {"inc", "corp", "llc", "ltd", "the", "company", "corporation"} and len(t) > 2
        ]
        parent = _apex(domain)
        for url in (f"https://www.{domain}/", f"https://{domain}/"):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(1500)
                hrefs = await page.eval_on_selector_all(
                    "a[href]",
                    "els => els.slice(0, 200).map(e => e.href)",
                )
            except Exception:  # noqa: BLE001
                continue
            for href in hrefs or []:
                if not isinstance(href, str) or not href.startswith("http"):
                    continue
                host = urlparse(href).hostname or ""
                if not host or _STOP.search(host):
                    continue
                d = _apex(host)
                if d == parent:
                    continue
                # Keep domains that share brand tokens or look like sister sites in footer
                if tokens and not any(t in d for t in tokens):
                    # Still keep if linked from same site with company-ish path
                    path = urlparse(href).path.lower()
                    if not any(x in path for x in ("brand", "subsidiar", "compan", "group")):
                        continue
                entry = found.setdefault(
                    d,
                    {
                        "domain": d,
                        "relationship_verified": False,
                        "confidence": "medium" if any(t in d for t in tokens) else "low",
                        "evidence": "Playwright homepage link discovery",
                        "sources": [],
                        "titles": [],
                    },
                )
                if href not in entry["sources"] and len(entry["sources"]) < 5:
                    entry["sources"].append(href)
            if found:
                break
        return list(found.values())

    async def run(
        self,
        company: str,
        domain: Optional[str] = None,
        engine: str = "bing",
        max_queries: int = 6,
        **kwargs: Any,
    ) -> dict[str, Any]:
        queries = self._queries(company, domain, max_queries)
        self.log.info(
            "search company=%r domain=%s engine=%s queries=%d",
            company,
            domain,
            engine,
            len(queries),
        )

        try:
            ctx = await self.ensure_browser()
        except Exception as e:  # noqa: BLE001
            self.log.error("playwright unavailable: %s", e)
            return {
                "company": company,
                "error": f"playwright unavailable: {e}",
                "queries": queries,
                "hits": [],
                "domains": [],
                "entity_hints": [],
            }

        page = await ctx.new_page()
        hits: list[dict[str, Any]] = []
        domains: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        try:
            # Homepage discovery first (stronger signal than SERP noise)
            if domain:
                try:
                    for entry in await self._homepage_domains(page, domain, company):
                        d = entry["domain"]
                        domains[d] = entry
                    self.log.debug("homepage discovery domains=%d", len(domains))
                except Exception as e:  # noqa: BLE001
                    errors.append(f"homepage:{e}")
                    self.log.warning("homepage discovery failed: %s", e)

            for qi, q in enumerate(queries, 1):
                self.log.info(
                    "progress [playwright_search] query %d/%d %s",
                    qi,
                    len(queries),
                    q,
                )
                try:
                    if engine == "duckduckgo":
                        links = await self._search_ddg(page, q)
                    else:
                        links = await self._search_bing(page, q)
                        if not links:
                            # Fallback engine when Bing is empty / rate-limited
                            self.log.info("bing empty for %r — trying duckduckgo", q)
                            links = await self._search_ddg(page, q)
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{q}: {e}")
                    self.log.warning("search failed %r: %s", q, e)
                    await incremental_sleep(0.4, reason="serp-error", logger=self.log)
                    continue

                for item in (links or [])[:10]:
                    href = item.get("href") or ""
                    if engine != "duckduckgo":
                        href = _unwrap_bing(href)
                    if not href.startswith("http"):
                        continue
                    host = urlparse(href).hostname or ""
                    if not host or _STOP.search(host):
                        continue
                    d = _apex(host)
                    hit = {
                        "title": item.get("title") or "",
                        "url": href,
                        "domain": d,
                        "query": q,
                    }
                    hits.append(hit)
                    entry = domains.setdefault(
                        d,
                        {
                            "domain": d,
                            "relationship_verified": False,
                            "confidence": "low",
                            "evidence": f"Playwright {engine} search",
                            "sources": [],
                            "titles": [],
                        },
                    )
                    if href not in entry["sources"] and len(entry["sources"]) < 5:
                        entry["sources"].append(href)
                    if hit["title"] and hit["title"] not in entry["titles"]:
                        entry["titles"].append(hit["title"])
                # Pace SERP requests
                await asyncio.sleep(1.2)
        finally:
            await page.close()

        company_tokens = [
            t
            for t in re.findall(r"[a-z0-9]+", company.lower())
            if t not in {"inc", "corp", "llc", "ltd", "the", "company", "corporation"}
            and len(t) >= 2
        ]
        for entry in domains.values():
            if len(entry["sources"]) >= 2:
                entry["confidence"] = "medium"
            blob = " ".join(entry.get("titles") or []).lower() + " " + entry["domain"]
            if company_tokens and sum(1 for t in company_tokens if t in blob) >= max(
                1, len(company_tokens) // 2
            ):
                entry["confidence"] = "medium"
            # Prefer domains that embed brand tokens
            if company_tokens and any(t in entry["domain"] for t in company_tokens):
                entry["confidence"] = "medium"

        domain_list = list(domains.values())
        recommended = self._recommend_primary(company, hits, domain_list)

        return {
            "company": company,
            "domain": domain,
            "engine": engine,
            "queries": queries,
            "hits": hits,
            "domains": domain_list,
            "recommended_domain": recommended,
            "entity_hints": [],
            "errors": errors,
            "error": None,
        }

    @staticmethod
    def _recommend_primary(
        company: str,
        hits: list[dict[str, Any]],
        domain_rows: list[dict[str, Any]],
    ) -> Optional[str]:
        """Pick best official-looking apex from SERP hits (first strong match)."""
        stop = {"inc", "corp", "llc", "ltd", "the", "company", "corporation", "group"}
        tokens = [
            t
            for t in re.findall(r"[a-z0-9]+", company.lower())
            if t not in stop and len(t) >= 2
        ]
        by_domain = {d.get("domain"): d for d in domain_rows if d.get("domain")}

        # Prefer SERP order, but only hostnames that embed a brand token
        # (avoids wikipedia/news titles matching the company name).
        for hit in hits:
            d = hit.get("domain")
            if not d or d not in by_domain:
                continue
            if tokens and any(t in d for t in tokens):
                return d

        # Fallback: best medium-confidence domain with brand token in hostname
        ranked = sorted(
            domain_rows,
            key=lambda e: (
                0 if (e.get("confidence") == "medium") else 1,
                0 if tokens and any(t in (e.get("domain") or "") for t in tokens) else 1,
                -(len(e.get("sources") or [])),
            ),
        )
        for e in ranked:
            d = e.get("domain")
            if d and tokens and any(t in d for t in tokens):
                return d
        return None

    async def close(self) -> None:
        await self.close_browser()
