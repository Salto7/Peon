"""Async orchestration pipeline — a Plugin that runs other Plugin modules."""

from __future__ import annotations

import asyncio
import html as html_lib
from datetime import datetime, timezone
from typing import Any

from ..config import PipelineConfig
from ..http import AsyncHttpClient
from ..log import get_logger
from ..plugin import Plugin
from ..report import finalize_report
from ..summary import _is_junk_entity_name
from ..util import apex_domain, is_apex_domain, normalize_domain
from . import create_plugin
from .entra import clear_oidc_cache

log = get_logger("pipeline")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _junk_entity(name: str) -> bool:
    return _is_junk_entity_name(name)


def _az_match_block(
    company_tid: str,
    *,
    issuer: str | None = None,
    oidc_url: str | None = None,
    error: str | None = None,
    tenant_id: str | None = None,
    matched: bool = True,
) -> dict[str, Any]:
    tid = tenant_id or (company_tid if matched else None)
    return {
        "tenant_found": bool(tid),
        "tenant_id": tid,
        "expected_tenant_id": company_tid,
        "matches_company_tenant": matched,
        "issuer": issuer,
        "oidc_url": oidc_url,
        "error": error,
    }


class CorpOsintPipeline(Plugin):
    """
    Orchestrator plugin.

    Stages:
      EDGAR → (no --domain: Playwright seed →) resolve parent →
      (with --domain: Playwright) → urlscan → DNSLytics GTM →
      parallel [RDAP | Azure | ASN] → tenant domains → dangling parked/Entra

    When ``--domain`` / MCP ``domain`` is omitted, Playwright runs before resolve
    to seed ``primary_domain``; inventing ``.com``/``.net`` is a last resort only.
    When a domain is supplied, resolve never invents and Playwright stays after.
    """

    name = "pipeline"
    description = "Full corporate OSINT orchestration across all plugins"

    def __init__(
        self,
        config: PipelineConfig | None = None,
        http: AsyncHttpClient | None = None,
        **kwargs: Any,
    ) -> None:
        if config is None:
            raise ValueError("PipelineConfig required")
        super().__init__(http=http, config=config, **kwargs)
        self.plugins: dict[str, Plugin] = {}
        # Convenience aliases (set in _init_plugins)
        self.edgar = self.resolver = self.urlscan = None
        self.web = self.dnslytics = self.rdap = None
        self.azure = self.asn = self.tenant_domains = self.dangling = None

    def _init_plugins(self, http: AsyncHttpClient) -> None:
        self.http = http
        cfg = self.config
        assert cfg is not None
        order = [
            "edgar",
            "resolve_domains",
            "playwright_search",
            "urlscan",
            "dnslytics_gtm",
            "rdap",
            "azure_tenant",
            "asn",
            "tenant_domains",
            "dangling_check",
        ]
        self.plugins = {name: create_plugin(name, http, cfg) for name in order}
        self.edgar = self.plugins["edgar"]
        self.resolver = self.plugins["resolve_domains"]
        self.web = self.plugins["playwright_search"]
        self.urlscan = self.plugins["urlscan"]
        self.dnslytics = self.plugins["dnslytics_gtm"]
        self.rdap = self.plugins["rdap"]
        self.azure = self.plugins["azure_tenant"]
        self.asn = self.plugins["asn"]
        self.tenant_domains = self.plugins["tenant_domains"]
        self.dangling = self.plugins["dangling_check"]
        # Apply dangling tuning from config
        self.dangling.concurrency = cfg.limits.dangling_concurrency  # type: ignore[attr-defined]
        self.dangling.check_rdap = cfg.limits.dangling_check_rdap  # type: ignore[attr-defined]

    @staticmethod
    def _add_domain(
        order: list[str],
        meta: dict[str, dict[str, Any]],
        domain: str,
        *,
        verified: bool,
        evidence: str,
        sources: list[str],
        confidence: str = "low",
        is_parked: bool = False,
    ) -> None:
        dom = normalize_domain(domain)
        if not dom:
            return
        if not is_apex_domain(dom):
            # Collapse hostnames (e.g. GTM-reverse or OIDC hits on a subdomain)
            # down to their registrable apex — a subdomain is not a new company
            # domain, and urlscan/web_search already apex-collapse their own
            # hits. Without this, dnslytics_gtm and the entra OIDC-verify stage
            # (neither of which apex-filters) leak raw hostnames into results.
            dom = apex_domain(dom) or dom
        if dom not in order:
            order.append(dom)
        prev = meta.get(dom)
        if not prev or (verified and not prev.get("relationship_verified")):
            meta[dom] = {
                "domain": dom,
                "relationship_verified": verified,
                "evidence": evidence,
                "sources": sources or [],
                "confidence": confidence,
                "is_parked": bool(is_parked or (prev or {}).get("is_parked")),
            }
        elif is_parked and prev:
            prev["is_parked"] = True
            if "park" not in (prev.get("evidence") or "").lower():
                prev["evidence"] = (prev.get("evidence") or "") + "; parking page"

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        try:
            return await self._run()
        finally:
            # Always release browsers, even when a stage raised unexpectedly.
            for plug in self.plugins.values():
                try:
                    await plug.close()
                except Exception as exc:  # noqa: BLE001
                    log.debug("plugin close failed: %s", exc)

    async def _run(self) -> dict[str, Any]:
        cfg = self.config
        assert cfg is not None
        errors: list[str] = []
        notes: list[str] = []
        entities: list[dict[str, Any]] = []
        parent: dict[str, Any] = {
            "name": cfg.company,
            "legal_name": cfg.company,
            "cik": None,
            "ticker": cfg.ticker,
            "primary_domain": cfg.domain,
            "sources": [],
        }

        log.info(
            "start company=%r domain=%s stock_market_ticker=%s cik=%s proxy=%s",
            cfg.company,
            cfg.domain,
            cfg.ticker,
            cfg.cik,
            bool(cfg.http.proxy),
        )
        clear_oidc_cache()
        async with AsyncHttpClient(cfg.http, cfg.throttle) as http:
            self._init_plugins(http)
            assert self.edgar and self.resolver and self.urlscan
            assert self.web and self.dnslytics and self.rdap and self.azure and self.asn
            assert self.tenant_domains and self.dangling
            log.debug("plugins ready: %s", ", ".join(self.plugins))

            # --- EDGAR ---
            if cfg.stages.edgar:
                log.info("stage edgar")
                try:
                    edgar = await self.edgar.run(
                        company=cfg.company, ticker=cfg.ticker, cik=cfg.cik
                    )
                    parent.update({k: v for k, v in (edgar.get("parent") or {}).items() if v is not None})
                    if cfg.domain:
                        parent["primary_domain"] = cfg.domain
                    for s in edgar.get("subsidiaries") or []:
                        name = html_lib.unescape((s.get("name") or "").strip())
                        if _junk_entity(name):
                            continue
                        entities.append(
                            {
                                "name": name,
                                "type": s.get("type", "subsidiary"),
                                "confidence": s.get("confidence", "high"),
                                "relationship_verified": bool(s.get("relationship_verified")),
                                "evidence": s.get("evidence"),
                                "sources": s.get("sources") or [],
                                "domains": [],
                            }
                        )
                    errors.extend(edgar.get("errors") or [])
                    log.info(
                        "edgar done parent=%r subsidiaries=%d",
                        parent.get("legal_name") or parent.get("name"),
                        len(entities),
                    )
                    if edgar.get("parent", {}).get("match") == "ambiguous":
                        notes.append("EDGAR match ambiguous; pass --cik or --ticker (stock market ticker symbol)")
                        parent["candidates"] = edgar["parent"].get("candidates")
                        log.warning("edgar match ambiguous; pass --cik or --ticker (stock market ticker symbol)")
                except Exception as e:  # noqa: BLE001
                    errors.append(f"EDGAR: {e}")
                    log.exception("edgar failed: %s", e)
            else:
                notes.append("EDGAR skipped")
                log.info("stage edgar skipped")

            work = [
                {
                    "name": parent.get("legal_name") or cfg.company,
                    "type": "parent",
                    "confidence": "high",
                    "relationship_verified": True,
                    "evidence": "Query parent",
                    "sources": parent.get("sources") or [],
                    "domains": [],
                }
            ]
            work.extend(entities[: cfg.limits.max_entities])

            domain_supplied = bool(cfg.domain)
            domain_order: list[str] = []
            domain_meta: dict[str, dict[str, Any]] = {}
            urlscan_block = playwright_block = None
            playwright_done = False

            async def _run_playwright(seed_domain: str | None, *, seed_primary: bool) -> None:
                nonlocal playwright_block, playwright_done
                log.info(
                    "stage playwright engine=%s queries=%d seed=%s",
                    cfg.playwright_engine,
                    cfg.limits.playwright_max_queries,
                    seed_domain or "(none)",
                )
                try:
                    playwright_block = await self.web.run(
                        cfg.company,
                        seed_domain,
                        engine=cfg.playwright_engine,
                        max_queries=cfg.limits.playwright_max_queries,
                    )
                    if playwright_block.get("error"):
                        errors.append(playwright_block["error"])
                        log.warning("playwright: %s", playwright_block["error"])
                    for d in playwright_block.get("domains") or []:
                        self._add_domain(
                            domain_order,
                            domain_meta,
                            d["domain"],
                            verified=False,
                            evidence=d.get("evidence") or "Playwright search",
                            sources=list(d.get("sources") or [])[:3],
                            confidence=d.get("confidence") or "low",
                        )
                    rec_dom = playwright_block.get("recommended_domain")
                    if seed_primary and rec_dom and not parent.get("primary_domain"):
                        # Candidate only — primary is set after resolve verifies
                        parent["_search_domain"] = rec_dom
                        self._add_domain(
                            domain_order,
                            domain_meta,
                            rec_dom,
                            verified=False,
                            evidence="Playwright recommended official site",
                            sources=["playwright_search"],
                            confidence="medium",
                        )
                        log.info("playwright search candidate=%s", rec_dom)
                    errors.extend(playwright_block.get("errors") or [])
                    log.info(
                        "playwright done domains=%d hits=%d recommended=%s",
                        len(playwright_block.get("domains") or []),
                        len(playwright_block.get("hits") or []),
                        rec_dom or "-",
                    )
                except Exception as e:  # noqa: BLE001
                    errors.append(f"playwright: {e}")
                    log.exception("playwright failed: %s", e)
                playwright_done = True

            # No --domain: discover official site via Playwright before inventing .com
            if not domain_supplied:
                if cfg.stages.playwright:
                    await _run_playwright(None, seed_primary=True)
                else:
                    notes.append("playwright skipped")
                    log.info("stage playwright skipped")

            # --- Resolve sites (bounded parallel) ---
            if cfg.stages.resolve_sites:
                search_dom = parent.get("_search_domain")
                # Prefer Playwright candidate; invent .com/.net only if search found nothing
                allow_guess = not domain_supplied and not search_dom and not parent.get(
                    "primary_domain"
                )
                parent_for_resolve = parent.get("primary_domain") or search_dom
                log.info(
                    "stage resolve (parent only) allow_guess=%s parent_domain=%s",
                    allow_guess,
                    parent_for_resolve or "-",
                )
                sem = asyncio.Semaphore(4)

                async def _resolve_one(
                    ent: dict[str, Any], *, guess: bool, parent_domain: str | None
                ) -> None:
                    async with sem:
                        try:
                            log.debug("resolve %s guess=%s", ent["name"], guess)
                            resolved = await self.resolver.run(
                                ent["name"],
                                parent_domain=parent_domain,
                                allow_guess=guess,
                            )
                            # Prefer verified site; still keep parked candidates for dangling checks
                            rec = resolved.get("recommended")
                            parked = [
                                d
                                for d in (resolved.get("domains") or [])
                                if d.get("is_parked") and d.get("domain")
                            ]
                            chosen = rec or (resolved.get("domains") or [None])[0]
                            ent_domains: list[dict[str, Any]] = []
                            if chosen and chosen.get("domain"):
                                ent_domains.append(
                                    {
                                        "domain": chosen.get("domain"),
                                        "relationship_verified": bool(
                                            chosen.get("relationship_verified")
                                        ),
                                        "evidence": chosen.get("evidence"),
                                        "sources": chosen.get("sources") or [],
                                        "confidence": chosen.get("confidence", "low"),
                                        "is_parked": bool(chosen.get("is_parked")),
                                    }
                                )
                            for p in parked:
                                if not any(x.get("domain") == p.get("domain") for x in ent_domains):
                                    ent_domains.append(
                                        {
                                            "domain": p.get("domain"),
                                            "relationship_verified": False,
                                            "evidence": p.get("evidence"),
                                            "sources": p.get("sources") or [],
                                            "confidence": "low",
                                            "is_parked": True,
                                        }
                                    )
                            ent["domains"] = ent_domains
                            if (
                                ent["type"] == "parent"
                                and rec
                                and rec.get("relationship_verified")
                            ):
                                parent["primary_domain"] = (
                                    parent.get("primary_domain") or rec.get("domain")
                                )
                        except Exception as e:  # noqa: BLE001
                            errors.append(f"resolve {ent['name']}: {e}")

                # Only resolve the parent — do NOT invent .com slugs for every subsidiary
                parents = [e for e in work if e.get("type") == "parent"]
                await asyncio.gather(
                    *[
                        _resolve_one(e, guess=allow_guess, parent_domain=parent_for_resolve)
                        for e in parents
                    ]
                )
                # Search candidate unverified → last-resort invent .com/.net
                if (
                    not domain_supplied
                    and not parent.get("primary_domain")
                    and search_dom
                    and parents
                ):
                    log.info("stage resolve invent fallback (search domain unverified)")
                    await asyncio.gather(
                        *[
                            _resolve_one(e, guess=True, parent_domain=search_dom)
                            for e in parents
                        ]
                    )

            if cfg.domain:
                work[0].setdefault("domains", []).insert(
                    0,
                    {
                        "domain": cfg.domain,
                        "relationship_verified": True,
                        "evidence": "Provided via --domain",
                        "sources": [],
                        "confidence": "high",
                    },
                )
                parent["primary_domain"] = cfg.domain

            for ent in work:
                for d in ent.get("domains") or []:
                    if d.get("domain"):
                        self._add_domain(
                            domain_order,
                            domain_meta,
                            d["domain"],
                            verified=bool(d.get("relationship_verified")),
                            evidence=d.get("evidence") or "",
                            sources=list(d.get("sources") or []),
                            confidence=d.get("confidence") or "low",
                            is_parked=bool(d.get("is_parked")),
                        )
            # Without --domain, never promote arbitrary SERP hits to primary
            if domain_supplied:
                primary = parent.get("primary_domain") or (
                    domain_order[0] if domain_order else None
                )
            else:
                primary = parent.get("primary_domain")
                if not primary and not cfg.stages.resolve_sites:
                    primary = parent.get("_search_domain")
                    if primary:
                        parent["primary_domain"] = primary

            # Domain supplied: Playwright after resolve (unchanged order)
            if domain_supplied:
                if cfg.stages.playwright and not playwright_done:
                    await _run_playwright(primary, seed_primary=False)
                elif not cfg.stages.playwright:
                    notes.append("playwright skipped")
                    log.info("stage playwright skipped")

            if cfg.stages.urlscan and (primary or cfg.company):
                log.info("stage urlscan domain=%s", primary)
                try:
                    urlscan_block = await self.urlscan.run(
                        domain=primary, company=cfg.company, size=cfg.limits.urlscan_size
                    )
                    for d in urlscan_block.get("domains") or []:
                        rel = d.get("relation")
                        self._add_domain(
                            domain_order,
                            domain_meta,
                            d["domain"],
                            verified=rel in {"same_registrable", "subdomain_tree"},
                            evidence=f"urlscan ({rel})",
                            sources=list(d.get("sources") or [])[:3],
                            confidence="medium" if rel != "related" else "low",
                        )
                    errors.extend(urlscan_block.get("errors") or [])
                    log.info(
                        "urlscan done domains=%d results=%s",
                        len(urlscan_block.get("domains") or []),
                        urlscan_block.get("result_count"),
                    )
                except Exception as e:  # noqa: BLE001
                    errors.append(f"urlscan: {e}")
                    log.exception("urlscan failed: %s", e)
            else:
                notes.append("urlscan skipped")
                log.info("stage urlscan skipped")

            # --- DNSLytics GTM ---
            dnslytics_block = None
            if cfg.stages.dnslytics and domain_order:
                seeds = [
                    d
                    for d in domain_order
                    if domain_meta.get(d, {}).get("relationship_verified")
                ] or domain_order
                log.info(
                    "stage dnslytics seeds=%d max_gtm=%d",
                    min(len(seeds), cfg.limits.dnslytics_max_domains),
                    cfg.limits.dnslytics_max_gtm_reverse,
                )
                try:
                    dnslytics_block = await self.dnslytics.run(
                        domains=seeds[: cfg.limits.dnslytics_max_domains],
                        max_domains=cfg.limits.dnslytics_max_domains,
                        max_gtm_reverse=cfg.limits.dnslytics_max_gtm_reverse,
                    )
                    for d in dnslytics_block.get("domains") or []:
                        self._add_domain(
                            domain_order,
                            domain_meta,
                            d["domain"],
                            verified=False,
                            evidence=d.get("evidence") or "GTM reverse (DNSLytics)",
                            sources=list(d.get("sources") or [])[:3],
                            confidence=d.get("confidence") or "medium",
                        )
                    errors.extend(dnslytics_block.get("errors") or [])
                    if dnslytics_block.get("errors"):
                        log.warning("dnslytics errors: %s", dnslytics_block["errors"][:5])
                    log.info(
                        "dnslytics done tags=%d domains=%d",
                        len(dnslytics_block.get("tags") or []),
                        len(dnslytics_block.get("domains") or []),
                    )
                except Exception as e:  # noqa: BLE001
                    errors.append(f"dnslytics: {e}")
                    dnslytics_block = {"error": str(e)}
                    log.exception("dnslytics failed: %s", e)
            else:
                notes.append("dnslytics skipped")
                log.info("stage dnslytics skipped")

            # Enrichment targets: verified first, always include parked for dangling Entra check
            enrich = [d for d in domain_order if domain_meta.get(d, {}).get("relationship_verified")]
            if not enrich:
                enrich = domain_order[: cfg.limits.max_enrich_domains]
            else:
                for d in domain_order:
                    if d not in enrich and len(enrich) < cfg.limits.max_enrich_domains:
                        enrich.append(d)
            if cfg.stages.dangling:
                for d in domain_order:
                    if domain_meta.get(d, {}).get("is_parked") and d not in enrich:
                        enrich.append(d)

            async def _enrich_one(
                dom: str,
                *,
                azure_only: bool = False,
                expected_tenant_id: str | None = None,
            ) -> dict[str, Any]:
                meta = domain_meta.get(dom) or {"domain": dom}
                az_kw = {"expected_tenant_id": expected_tenant_id} if expected_tenant_id else {}
                if azure_only:
                    tasks = {"azure": self.azure.enrich(dom, **az_kw)}
                else:
                    tasks = {
                        "rdap": self.rdap.enrich(dom),
                        "azure": self.azure.enrich(dom, **az_kw),
                    }
                    if cfg.stages.asn and not meta.get("is_parked"):
                        tasks["asn"] = self.asn.enrich(
                            dom,
                            company=parent.get("legal_name") or cfg.company,
                            deep=cfg.asn_deep,
                        )
                keys = list(tasks.keys())
                parts = await asyncio.gather(*[tasks[k] for k in keys], return_exceptions=True)
                out: dict[str, Any] = {
                    "domain": dom,
                    "discovery": meta,
                    "rdap": None,
                    "whois": None,
                    "azure_tenant": None,
                    "asn": None,
                    "is_parked": bool(meta.get("is_parked")),
                    "dangling_entra": False,
                    "dangling_entra_detail": None,
                }
                for k, part in zip(keys, parts, strict=True):
                    if isinstance(part, Exception):
                        errors.append(f"{k}:{dom}: {part}")
                        continue
                    if k == "rdap":
                        out["rdap"] = part.get("rdap")
                        out["whois"] = part.get("whois")
                    elif k == "azure":
                        out["azure_tenant"] = part.get("azure_tenant")
                    elif k == "asn":
                        out["asn"] = {
                            "resolved_ips": part.get("resolved_ips"),
                            "asns": part.get("asns"),
                        }
                        errors.extend(part.get("errors") or [])
                return out

            log.info("stage enrich domains=%d", len(enrich))
            domains_out: list[dict[str, Any]] = list(
                await asyncio.gather(*[_enrich_one(d) for d in enrich])
            )
            log.debug("enrich done rows=%d", len(domains_out))

            # Company tenant IDs only from verified / primary domains (not parked candidates)
            company_tenants: dict[str, str] = {}
            for row in domains_out:
                az = row.get("azure_tenant") or {}
                tid = az.get("tenant_id")
                disc = row.get("discovery") or {}
                if not tid or row.get("is_parked"):
                    continue
                if disc.get("relationship_verified") or row["domain"] == primary:
                    company_tenants[tid] = row["domain"]
            if company_tenants:
                log.info("company entra tenants=%s", list(company_tenants))
                parent["entra_tenant_id"] = parent.get("entra_tenant_id") or next(
                    iter(company_tenants)
                )

            # --- IdefixTools tenant → more domains ---
            tenant_block = None
            if cfg.stages.tenant_domains and (company_tenants or primary):
                tid = next(iter(company_tenants), None)
                log.info("stage tenant_domains tenant=%s domain=%s", tid, primary)
                try:
                    tenant_block = await self.tenant_domains.run(
                        tenant_id=tid,
                        domain=primary,
                        max_domains=cfg.limits.tenant_domains_max,
                    )
                    if tenant_block.get("tenant_id") and tenant_block["tenant_id"] not in company_tenants:
                        company_tenants[tenant_block["tenant_id"]] = primary or ""
                    parent["entra_tenant_id"] = tenant_block.get("tenant_id") or next(
                        iter(company_tenants), None
                    )
                    parent["entra_display_name"] = tenant_block.get("display_name")
                    for d in tenant_block.get("domains") or []:
                        self._add_domain(
                            domain_order,
                            domain_meta,
                            d["domain"],
                            verified=True,
                            evidence=d.get("evidence") or "Entra verified domain (IdefixTools)",
                            sources=list(d.get("sources") or [])[:3],
                            confidence="high",
                        )
                    errors.extend(tenant_block.get("errors") or [])
                    log.info(
                        "tenant_domains done count=%s display=%s",
                        tenant_block.get("domain_count")
                        or len(tenant_block.get("domains") or []),
                        tenant_block.get("display_name"),
                    )
                except Exception as e:  # noqa: BLE001
                    errors.append(f"tenant_domains: {e}")
                    tenant_block = {"error": str(e)}
                    log.exception("tenant_domains failed: %s", e)
            else:
                notes.append("tenant domain expansion skipped")
                log.info("stage tenant_domains skipped")

            # --- OIDC-verify all found domains against company Entra tenant ---
            entra_verify_block: dict[str, Any] | None = None
            company_tid = parent.get("entra_tenant_id") or next(iter(company_tenants), None)
            if cfg.stages.entra_verify and company_tid:
                log.info("stage entra_oidc_verify tenant=%s", company_tid)
                evid_oidc = f"OIDC issuer matches company tenant {company_tid}"
                already_checked: set[str] = set()
                for row in domains_out:
                    az = row.get("azure_tenant") or {}
                    if not az.get("tenant_id"):
                        continue
                    already_checked.add(row["domain"])
                    match = az["tenant_id"] == company_tid
                    az["expected_tenant_id"] = company_tid
                    az["matches_company_tenant"] = match
                    if match:
                        disc = row.setdefault("discovery", {})
                        disc["relationship_verified"] = True
                        disc["confidence"] = "high"
                        if "OIDC" not in (disc.get("evidence") or ""):
                            prev = disc.get("evidence") or ""
                            disc["evidence"] = f"{prev}; {evid_oidc}" if prev else evid_oidc
                        domain_meta[row["domain"]] = {
                            **(domain_meta.get(row["domain"]) or {}),
                            **disc,
                        }

                candidates = list(domain_order)
                for d in (tenant_block or {}).get("domains") or []:
                    name = d.get("domain") if isinstance(d, dict) else d
                    if name:
                        candidates.append(str(name))
                try:
                    entra_verify_block = await self.azure.verify_domains(
                        candidates,
                        company_tid,
                        concurrency=cfg.limits.entra_verify_concurrency,
                        max_domains=cfg.limits.entra_verify_max,
                        skip=already_checked,
                    )
                    existing = {r["domain"]: r for r in domains_out}
                    for hit in entra_verify_block.get("results") or []:
                        dom = hit["domain"]
                        if hit.get("matches_company_tenant"):
                            self._add_domain(
                                domain_order,
                                domain_meta,
                                dom,
                                verified=True,
                                evidence=evid_oidc,
                                sources=[hit["oidc_url"]] if hit.get("oidc_url") else [],
                                confidence="high",
                            )
                            az = _az_match_block(
                                company_tid,
                                issuer=hit.get("issuer"),
                                oidc_url=hit.get("oidc_url"),
                            )
                            if dom in existing:
                                row = existing[dom]
                                row.setdefault("azure_tenant", {}).update(az)
                                disc = row.setdefault("discovery", {})
                                disc["relationship_verified"] = True
                                disc["confidence"] = "high"
                            else:
                                domains_out.append(
                                    {
                                        "domain": dom,
                                        "discovery": domain_meta.get(dom)
                                        or {
                                            "domain": dom,
                                            "relationship_verified": True,
                                            "confidence": "high",
                                            "evidence": evid_oidc,
                                            "sources": [hit["oidc_url"]]
                                            if hit.get("oidc_url")
                                            else [],
                                        },
                                        "rdap": None,
                                        "whois": None,
                                        "azure_tenant": az,
                                        "asn": None,
                                        "is_parked": False,
                                        "dangling_entra": False,
                                        "dangling_entra_detail": None,
                                    }
                                )
                        elif hit.get("tenant_id") and dom in existing:
                            existing[dom].setdefault("azure_tenant", {}).update(
                                _az_match_block(
                                    company_tid,
                                    issuer=hit.get("issuer"),
                                    oidc_url=hit.get("oidc_url"),
                                    error=hit.get("error"),
                                    tenant_id=hit.get("tenant_id"),
                                    matched=False,
                                )
                            )
                    counts = entra_verify_block.get("counts") or {}
                    log.info(
                        "entra_oidc_verify done matched=%s mismatched=%s not_entra=%s",
                        counts.get("matched"),
                        counts.get("mismatched"),
                        counts.get("not_entra"),
                    )
                except Exception as e:  # noqa: BLE001
                    errors.append(f"entra_oidc_verify: {e}")
                    entra_verify_block = {"error": str(e)}
                    log.exception("entra_oidc_verify failed: %s", e)
            elif not company_tid:
                notes.append("entra OIDC verify skipped (no company tenant)")
                log.info("stage entra_oidc_verify skipped (no tenant)")
            else:
                notes.append("entra OIDC verify skipped")
                log.info("stage entra_oidc_verify skipped")

            # --- Dangling: Entra-verified + parked / NXDOMAIN / unregistered / unreachable ---
            dangling: list[dict[str, Any]] = []
            dangling_block: dict[str, Any] | None = None
            if cfg.stages.dangling:
                log.info("stage dangling_check max=%d", cfg.limits.dangling_max_domains)
                tid = parent.get("entra_tenant_id") or next(iter(company_tenants), None)
                # Prefer OIDC-matched domains, then Idefix tenant list, then enriched rows
                entra_domains: list[str] = []
                seen_ed: set[str] = set()

                def _add_ed(name: str | None) -> None:
                    n = normalize_domain(name)
                    if not n or n in seen_ed or n.endswith(".onmicrosoft.com"):
                        return
                    seen_ed.add(n)
                    entra_domains.append(n)

                for d in (entra_verify_block or {}).get("matched") or []:
                    _add_ed(d)
                for d in ((tenant_block or {}).get("domains") or []):
                    _add_ed(d.get("domain") if isinstance(d, dict) else d)
                for row in domains_out:
                    az = row.get("azure_tenant") or {}
                    if az.get("matches_company_tenant") or (
                        az.get("tenant_id") and az["tenant_id"] in company_tenants
                    ):
                        _add_ed(row.get("domain"))
                _add_ed(primary)

                # Domains already marked parked during resolve (may not be Entra yet)
                parked_seeds = [
                    d
                    for d, meta in domain_meta.items()
                    if meta.get("is_parked")
                ]

                try:
                    dangling_block = await self.dangling.run(
                        domains=parked_seeds,
                        entra_verified_domains=entra_domains,
                        tenant_id=tid,
                        max_domains=cfg.limits.dangling_max_domains,
                    )
                    dangling = list(dangling_block.get("dangling_domains") or [])
                    # Merge flags onto already-enriched rows only (do not duplicate
                    # every dangling domain into domains_out — that balloons JSON)
                    by_dom = {r["domain"]: r for r in dangling_block.get("results") or []}
                    for row in domains_out:
                        hit = by_dom.get(row["domain"])
                        if not hit:
                            continue
                        row["is_parked"] = bool(hit.get("is_parked") or row.get("is_parked"))
                        row["dangling_kind"] = hit.get("kind")
                        row["dangling_entra"] = bool(hit.get("dangling_entra"))
                        row["dangling_entra_detail"] = hit.get("dangling_entra_detail")
                        if row.get("discovery") is not None:
                            row["discovery"]["is_parked"] = row["is_parked"]
                            row["discovery"]["dangling_entra"] = row["dangling_entra"]
                            if hit.get("parking_marker"):
                                row["discovery"]["parking_marker"] = hit["parking_marker"]
                    errors.extend(dangling_block.get("errors") or [])
                    log.info(
                        "dangling_check done scanned=%s dangling=%s counts=%s",
                        dangling_block.get("scanned"),
                        len(dangling),
                        dangling_block.get("counts"),
                    )
                except Exception as e:  # noqa: BLE001
                    errors.append(f"dangling_check: {e}")
                    dangling_block = {"error": str(e)}
                    log.exception("dangling_check failed: %s", e)
            else:
                notes.append("dangling parked/Entra check skipped")
                log.info("stage dangling_check skipped")

        entities_out = [
            e
            for e in work
            if e.get("type") != "parent"
            and not _junk_entity(e.get("name") or "")
        ]
        # Drop invented unresolved domain stubs from entity objects
        for e in entities_out:
            e["domains"] = [
                d
                for d in (e.get("domains") or [])
                if d.get("relationship_verified")
                or d.get("is_parked")
                or (d.get("sources") and d.get("confidence") in {"medium", "high"})
            ]

        parent.pop("_search_domain", None)

        raw = {
            "query": cfg.company,
            "generated_at": _now(),
            "parent": parent,
            "entities": entities_out,
            "domains": list(domains_out),
            "urlscan": urlscan_block,
            "playwright": {
                "queries": (playwright_block or {}).get("queries"),
                "domains": (playwright_block or {}).get("domains"),
                "recommended_domain": (playwright_block or {}).get("recommended_domain"),
                "hit_count": len((playwright_block or {}).get("hits") or []),
                "error": (playwright_block or {}).get("error"),
            }
            if playwright_block is not None
            else None,
            "dnslytics": dnslytics_block,
            "tenant_domains": tenant_block,
            "entra_verify": {
                "expected_tenant_id": (entra_verify_block or {}).get("expected_tenant_id"),
                "counts": (entra_verify_block or {}).get("counts"),
                "matched": (entra_verify_block or {}).get("matched"),
                "mismatched": (entra_verify_block or {}).get("mismatched"),
                "error": (entra_verify_block or {}).get("error"),
            }
            if entra_verify_block is not None
            else None,
            "dangling_check": {
                "scanned": (dangling_block or {}).get("scanned"),
                "counts": (dangling_block or {}).get("counts"),
                "error": (dangling_block or {}).get("error"),
            }
            if dangling_block is not None
            else None,
            "dangling_domains": [
                {
                    "domain": d.get("domain"),
                    "kind": d.get("kind") or d.get("dangling_kind"),
                    "tenant_id": d.get("tenant_id") or parent.get("entra_tenant_id"),
                    "is_parked": bool(d.get("is_parked")),
                    "parking_marker": d.get("parking_marker"),
                    "evidence": d.get("dangling_entra_detail") or d.get("evidence"),
                    "dns": d.get("dns"),
                    "http": d.get("http") or d.get("http_probe"),
                }
                for d in dangling
            ],
            "notes": notes,
            "errors": errors,
        }
        # Compact JSON + guarantee summary (always first keys)
        compact = bool(getattr(cfg, "compact_report", True))
        report = finalize_report(raw, compact=compact)
        log.info(
            "done domains=%d entities=%d dangling=%d errors=%d compact=%s",
            len(report.get("domains") or []),
            len(report.get("entities") or []),
            len(report.get("dangling_domains") or []),
            len(errors),
            compact,
        )
        if errors:
            log.warning("completed with %d error(s): %s", len(errors), errors[:8])
        return report
