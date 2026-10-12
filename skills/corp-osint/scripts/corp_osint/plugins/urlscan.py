"""urlscan.io related-domain discovery (sequential + 429-aware)."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Optional
from urllib.parse import quote, urlparse

from ..plugin import Plugin
from ..http import AsyncHttpClient, RateLimitedError
from ..util import normalize_domain

SEARCH = "https://urlscan.io/api/v1/search/"
_NOISE = re.compile(
    r"(?i)(^|\.)(google\.|gstatic\.|googleapis\.|facebook\.|linkedin\.|youtube\.|"
    r"cloudflare\.|amazonaws\.|azure\.|microsoftonline\.|doubleclick\.)"
)


class UrlscanLookup(Plugin):
    name = "urlscan"

    def __init__(self, http: AsyncHttpClient, config=None, api_key: Optional[str] = None, **kwargs) -> None:
        super().__init__(http, config=config, **kwargs)
        self.api_key = api_key or os.environ.get("URLSCAN_API_KEY")

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json"}
        if self.api_key:
            h["API-KEY"] = self.api_key
        return h

    @staticmethod
    def apex(host: str) -> str:
        host = normalize_domain(host) or host
        parts = host.split(".")
        if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "net"} and parts[-1] in {
            "uk", "au", "jp", "br", "il",
        }:
            return ".".join(parts[-3:])
        return ".".join(parts[-2:]) if len(parts) >= 2 else host

    async def search(self, query: str, size: int = 40) -> dict[str, Any]:
        url = f"{SEARCH}?q={quote(query)}&size={size}"
        try:
            status, body, _ = await self.http.get(url, headers=self._headers())
        except RateLimitedError as e:
            return {"query": query, "results": [], "error": f"rate limited: {e}"}
        except Exception as e:  # noqa: BLE001
            return {"query": query, "results": [], "error": str(e)}
        if status == 429:
            return {"query": query, "results": [], "error": "rate limited"}
        if status >= 400:
            return {"query": query, "results": [], "error": f"HTTP {status}"}
        doc = json.loads(body.decode("utf-8", errors="replace"))
        return {
            "query": query,
            "results": doc.get("results") or [],
            "error": None,
            "total": doc.get("total"),
        }

    async def run(
        self,
        domain: Optional[str] = None,
        company: Optional[str] = None,
        size: int = 40,
        **kwargs: Any,
    ) -> dict[str, Any]:
        queries: list[str] = []
        if domain:
            d = normalize_domain(domain) or domain
            apex = self.apex(d)
            queries.extend([f"domain:{d}", f"page.apexDomain:{apex}"])
        elif company:
            queries.append(f'page.title:"{company.replace(chr(34), "")}"')

        # Sequential — parallel urlscan queries amplify 429s
        responses: list[dict[str, Any]] = []
        for q in queries:
            responses.append(await self.search(q, size=size))
            await asyncio.sleep(0.4)

        seen: set[str] = set()
        results: list[dict[str, Any]] = []
        errors: list[str] = []
        for resp in responses:
            if resp.get("error"):
                errors.append(f"{resp['query']}: {resp['error']}")
            for r in resp.get("results") or []:
                rid = str(r.get("_id") or "")
                if rid and rid in seen:
                    continue
                if rid:
                    seen.add(rid)
                results.append(r)

        parent_apex = self.apex(domain) if domain else None
        tokens = [
            t
            for t in re.findall(r"[a-z0-9]+", (company or "").lower())
            if t not in {"inc", "corp", "llc", "ltd", "the", "company"} and len(t) > 2
        ]
        found: dict[str, dict[str, Any]] = {}
        for r in results:
            page = r.get("page") or {}
            task = r.get("task") or {}
            hosts = [page.get("domain"), page.get("apexDomain"), task.get("domain")]
            url = page.get("url") or task.get("url")
            if url:
                hosts.append(urlparse(url).hostname)
            for host in hosts:
                if not host:
                    continue
                apex = self.apex(str(host))
                if not apex or _NOISE.search(apex):
                    continue
                relation = "related"
                if parent_apex and (apex == parent_apex or str(host).endswith("." + parent_apex)):
                    relation = "same_registrable" if apex == parent_apex else "subdomain_tree"
                elif tokens and any(t in apex for t in tokens):
                    relation = "name_match"
                else:
                    continue
                entry = found.setdefault(
                    apex,
                    {
                        "domain": apex,
                        "relation": relation,
                        "examples": [],
                        "ips": [],
                        "asns": [],
                        "sources": [],
                    },
                )
                if url and url not in entry["examples"] and len(entry["examples"]) < 3:
                    entry["examples"].append(url)
                if page.get("ip") and page["ip"] not in entry["ips"]:
                    entry["ips"].append(page["ip"])
                uuid = r.get("_id") or task.get("uuid")
                if uuid:
                    src = f"https://urlscan.io/result/{uuid}/"
                    if src not in entry["sources"]:
                        entry["sources"].append(src)

        rank = {"same_registrable": 0, "subdomain_tree": 1, "name_match": 2}
        domains = sorted(found.values(), key=lambda x: (rank.get(x["relation"], 9), x["domain"]))
        return {
            "domain": domain,
            "company": company,
            "queries": queries,
            "result_count": len(results),
            "domains": domains,
            "errors": errors,
        }
