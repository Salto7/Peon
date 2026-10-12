"""Compact report summary — company, domains, sources/evidence, confidence, verified."""

from __future__ import annotations

import html as html_lib
from typing import Any


def _is_junk_entity_name(name: str) -> bool:
    n = name.lower().strip()
    junk_exact = {
        "name",
        "names",
        "subsidiary",
        "subsidiaries",
        "name of the subsidiary",
        "name of subsidiary",
        "subsidiary name",
        "company name",
        "where incorporated",
        "jurisdiction",
        "jurisdiction of incorporation",
        "percent ownership",
        "percentage ownership",
        "ownership interest",
        "exhibit 21",
    }
    if n in junk_exact:
        return True
    prefixes = (
        "name of the subsidiary",
        "name of subsidiary",
        "subsidiary name",
        "where incorporated",
        "jurisdiction",
        "percent ownership",
        "ownership interest",
        "exhibit 21",
        "subsidiaries of",
    )
    return any(n.startswith(p) for p in prefixes)


def _is_junk_domain(domain: str) -> bool:
    d = (domain or "").lower().strip().removeprefix("www.")
    if not d or "." not in d:
        return True
    label = d.split(".", 1)[0]
    # Invented from numbered holding cos / exhibit headers
    if label[:1].isdigit() or len(label) > 40:
        return True
    bad_slugs = (
        "nameofsubsidiary",
        "nameofth",
        "undefined",
        "null.",
        "example.com",
        "example.org",
        "localhost",
    )
    return any(x in d for x in bad_slugs)


def _domain_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per domain: domain, sources, evidence, confidence, verified."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(
        domain: str,
        *,
        verified: bool,
        confidence: str,
        sources: list[str],
        evidence: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        d = (domain or "").lower().strip().removeprefix("www.")
        if not d or d in seen or _is_junk_domain(d):
            return
        seen.add(d)
        item: dict[str, Any] = {
            "domain": d,
            "sources": [s for s in sources if s][:5],
            "evidence": (evidence or "").strip(),
            "confidence": confidence or "low",
            "verified": bool(verified),
        }
        if extra:
            for k, v in extra.items():
                if v not in (None, False, "", [], {}):
                    item[k] = v
        rows.append(item)

    parent = report.get("parent") or {}
    primary = parent.get("primary_domain")
    if primary:
        add(
            primary,
            verified=True,
            confidence="high",
            sources=list(parent.get("sources") or []),
            evidence="Primary / query domain",
        )

    for d in report.get("domains") or []:
        disc = d.get("discovery") or {}
        az = d.get("azure_tenant") or {}
        sources = list(disc.get("sources") or [])
        if az.get("oidc_url"):
            sources.append(az["oidc_url"])
        if (d.get("rdap") or {}).get("rdap_url"):
            sources.append(d["rdap"]["rdap_url"])
        evidence = disc.get("evidence") or ""
        if d.get("dangling_entra"):
            evidence = d.get("dangling_entra_detail") or evidence
        conf = disc.get("confidence") or (
            "high" if disc.get("relationship_verified") else "low"
        )
        verified = bool(
            disc.get("relationship_verified")
            or d.get("dangling_entra")
            or (az.get("tenant_found") and d.get("domain") == primary)
        )
        # Skip invented/unreachable slug noise
        if not verified and not disc.get("sources"):
            if "Name or service not known" in (evidence or "") or conf == "low":
                continue
        add(
            d.get("domain") or "",
            verified=verified,
            confidence=conf,
            sources=sources,
            evidence=evidence,
            extra={
                "is_parked": bool(d.get("is_parked")),
                "dangling_entra": bool(d.get("dangling_entra")),
                "tenant_id": az.get("tenant_id"),
            },
        )

    tenant_block = report.get("tenant_domains") or {}
    tenant_sources = list(tenant_block.get("sources") or [])[:2]
    for d in tenant_block.get("domains") or []:
        # Compact reports store plain domain strings
        if isinstance(d, str):
            dom, conf, evid, srcs = d, "high", "Entra verified domain", tenant_sources
        else:
            dom = d.get("domain") or ""
            conf = d.get("confidence") or "high"
            evid = d.get("evidence") or "Entra verified domain"
            srcs = list(d.get("sources") or tenant_sources)[:3]
        add(
            dom,
            verified=True,
            confidence=conf,
            sources=srcs,
            evidence=evid,
            extra={"tenant_id": tenant_block.get("tenant_id")},
        )

    for dang in report.get("dangling_domains") or []:
        add(
            dang.get("domain") or "",
            verified=True,
            confidence="high",
            sources=list(dang.get("sources") or [])[:3],
            evidence=dang.get("evidence") or "Parked domain in company Entra tenant",
            extra={
                "tenant_id": dang.get("tenant_id"),
                "is_parked": True,
                "dangling_entra": True,
            },
        )

    # Verified / high confidence first
    rank = {"high": 0, "medium": 1, "low": 2}
    rows.sort(key=lambda r: (0 if r["verified"] else 1, rank.get(r["confidence"], 3), r["domain"]))
    return rows


def build_summary(report: dict[str, Any], *, max_domains: int = 40) -> dict[str, Any]:
    """
    Small precise summary:

    - company (name)
    - domains[] each with: domain, sources, evidence, confidence, verified
    - entities[] (subsidiary names from EDGAR — no invented domains)
    """
    parent = report.get("parent") or {}
    company = (
        parent.get("legal_name")
        or parent.get("name")
        or report.get("query")
        or report.get("company")
        or report.get("plugin")
        or "unknown"
    )
    domain_items = _domain_rows(report)[:max_domains]

    entities: list[dict[str, Any]] = []
    for e in report.get("entities") or []:
        name = html_lib.unescape((e.get("name") or "").strip())
        if not name or _is_junk_entity_name(name):
            continue
        verified_domains = [
            d.get("domain")
            for d in (e.get("domains") or [])
            if d.get("relationship_verified") and d.get("domain") and not _is_junk_domain(d["domain"])
        ]
        entities.append(
            {
                "name": name,
                "domains": verified_domains,
                "sources": list(e.get("sources") or [])[:3],
                "evidence": e.get("evidence") or "",
                "confidence": e.get("confidence") or "medium",
                "verified": bool(e.get("relationship_verified")),
            }
        )

    return {
        "company": company,
        "ticker": parent.get("ticker"),
        "cik": parent.get("cik"),
        "primary_domain": parent.get("primary_domain"),
        "entra_tenant_id": parent.get("entra_tenant_id"),
        "domains": domain_items,
        "entities": entities[:50],
        "counts": {
            "domains": len(domain_items),
            "entities": len(entities),
            "dangling": len(report.get("dangling_domains") or []),
        },
        "dangling": [
            {
                "domain": d.get("domain"),
                "kind": d.get("kind"),
                "evidence": d.get("evidence"),
                "verified": True,
                "confidence": "high",
                "sources": [],
            }
            for d in (report.get("dangling_domains") or [])[:40]
        ],
    }


def format_summary_text(summary: dict[str, Any]) -> str:
    """Human-readable compact summary for agents/CLI."""
    lines = [f"Company: {summary.get('company')}"]
    if summary.get("ticker"):
        lines.append(f"Ticker: {summary['ticker']}")
    if summary.get("primary_domain"):
        lines.append(f"Primary domain: {summary['primary_domain']}")
    if summary.get("entra_tenant_id"):
        lines.append(f"Entra tenant: {summary['entra_tenant_id']}")
    lines.append("")
    lines.append("Domains:")
    for item in summary.get("domains") or []:
        ver = "yes" if item.get("verified") else "no"
        flags = []
        if item.get("dangling_entra"):
            flags.append("dangling_entra")
        if item.get("is_parked"):
            flags.append("parked")
        flag_s = f" [{', '.join(flags)}]" if flags else ""
        lines.append(
            f"- {item.get('domain')} | verified={ver} | "
            f"confidence={item.get('confidence')}{flag_s}"
        )
        if item.get("evidence"):
            lines.append(f"  evidence: {item['evidence'][:160]}")
        srcs = item.get("sources") or []
        if srcs:
            lines.append(f"  sources: {', '.join(srcs[:3])}")
    if summary.get("entities"):
        lines.append("")
        lines.append(f"Entities ({len(summary['entities'])}):")
        for e in summary["entities"][:15]:
            ver = "yes" if e.get("verified") else "no"
            lines.append(
                f"- {e.get('name')} | verified={ver} | confidence={e.get('confidence')}"
            )
        if len(summary["entities"]) > 15:
            lines.append(f"  … +{len(summary['entities']) - 15} more")
    dangling = summary.get("dangling") or []
    if dangling:
        lines.append("")
        lines.append(f"Dangling Entra ({len(dangling)} shown):")
        for d in dangling[:20]:
            lines.append(f"- {d.get('domain')} | kind={d.get('kind')}")
            if d.get("evidence"):
                lines.append(f"  evidence: {d['evidence'][:160]}")
    return "\n".join(lines)
