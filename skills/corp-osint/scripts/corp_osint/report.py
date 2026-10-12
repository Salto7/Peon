"""Compact report shaping — keep JSON small; always attach summary."""

from __future__ import annotations

from typing import Any

from .summary import build_summary, format_summary_text


def _trim(s: Any, n: int = 160) -> str:
    t = str(s or "").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def _compact_rdap(rdap: Any) -> Any:
    if not isinstance(rdap, dict):
        return rdap
    keep = (
        "registrar",
        "registrant_org",
        "registrant_country",
        "registered",
        "expires",
        "nameservers",
        "status",
        "rdap_url",
        "error",
    )
    out = {k: rdap[k] for k in keep if rdap.get(k) not in (None, "", [], {})}
    if rdap.get("nameservers"):
        out["nameservers"] = list(rdap["nameservers"])[:6]
    if rdap.get("status"):
        out["status"] = list(rdap["status"])[:6]
    return out or None


def _compact_whois(whois: Any) -> Any:
    if not isinstance(whois, dict):
        return whois
    # Drop raw_excerpt — biggest bloat
    out = {
        k: whois[k]
        for k in ("used_fallback", "registrant_org", "registrar", "org", "netname", "country", "error")
        if whois.get(k) not in (None, "", False)
    }
    if whois.get("used_fallback"):
        out["used_fallback"] = True
    return out or None


def _compact_azure(az: Any) -> Any:
    if not isinstance(az, dict):
        return az
    realm = az.get("user_realm") or {}
    out = {
        k: az[k]
        for k in (
            "tenant_found",
            "tenant_id",
            "expected_tenant_id",
            "matches_company_tenant",
            "issuer",
            "tenant_region_scope",
            "oidc_url",
            "error",
        )
        if az.get(k) not in (None, "", False)
    }
    if az.get("matches_company_tenant") is False:
        out["matches_company_tenant"] = False
    if realm:
        slim_realm = {
            k: realm[k]
            for k in ("NameSpaceType", "FederationBrandName", "DomainName")
            if realm.get(k) is not None
        }
        if slim_realm:
            out["user_realm"] = slim_realm
    if az.get("tenant_found") is False:
        out["tenant_found"] = False
    return out or None


def _compact_asn(asn: Any) -> Any:
    if not isinstance(asn, dict):
        return asn
    asns = []
    for a in asn.get("asns") or []:
        item = {
            k: a[k]
            for k in (
                "asn",
                "name",
                "description",
                "country_code",
                "ownership_verified",
                "ownership_confidence",
            )
            if a.get(k) not in (None, "", [], {})
        }
        ips = list(a.get("observed_ips") or [])[:4]
        if ips:
            item["observed_ips"] = ips
        asns.append(item)
    out: dict[str, Any] = {}
    if asn.get("resolved_ips"):
        out["resolved_ips"] = list(asn["resolved_ips"])[:6]
    if asns:
        out["asns"] = asns[:5]
    return out or None


def _compact_discovery(disc: Any) -> Any:
    if not isinstance(disc, dict):
        return disc
    out = {
        k: disc[k]
        for k in (
            "domain",
            "relationship_verified",
            "confidence",
            "is_parked",
            "dangling_entra",
            "parking_marker",
        )
        if disc.get(k) not in (None, "", False)
    }
    if disc.get("evidence"):
        out["evidence"] = _trim(disc["evidence"], 180)
    if disc.get("sources"):
        out["sources"] = list(disc["sources"])[:3]
    if disc.get("relationship_verified") is False:
        out["relationship_verified"] = False
    return out or None


def _compact_domain_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"domain": row.get("domain")}
    if row.get("discovery"):
        out["discovery"] = _compact_discovery(row["discovery"])
    if row.get("rdap"):
        out["rdap"] = _compact_rdap(row["rdap"])
    if row.get("whois"):
        out["whois"] = _compact_whois(row["whois"])
    if row.get("azure_tenant"):
        out["azure_tenant"] = _compact_azure(row["azure_tenant"])
    if row.get("asn"):
        out["asn"] = _compact_asn(row["asn"])
    for k in ("is_parked", "dangling_entra", "dangling_kind"):
        if row.get(k) not in (None, False, ""):
            out[k] = row[k]
    if row.get("dangling_entra_detail"):
        out["dangling_entra_detail"] = _trim(row["dangling_entra_detail"], 200)
    # Intentionally omit dns / http_probe blobs from domain rows
    return out


def _compact_tenant_domains(block: Any) -> Any:
    if not isinstance(block, dict):
        return block
    domains = block.get("domains") or []
    return {
        "tenant_id": block.get("tenant_id"),
        "display_name": block.get("display_name"),
        "default_domain": block.get("default_domain"),
        "domain_count": block.get("domain_count") or len(domains),
        "domains": [d.get("domain") if isinstance(d, dict) else d for d in domains],
        "sources": list(block.get("sources") or [])[:2],
        "errors": list(block.get("errors") or [])[:10],
    }


def _compact_dangling(rows: list[Any]) -> list[dict[str, Any]]:
    out = []
    for d in rows or []:
        if not isinstance(d, dict):
            continue
        item = {
            "domain": d.get("domain"),
            "kind": d.get("kind") or d.get("dangling_kind"),
            "tenant_id": d.get("tenant_id"),
            "is_parked": bool(d.get("is_parked")),
        }
        if d.get("parking_marker"):
            item["parking_marker"] = d["parking_marker"]
        if d.get("evidence") or d.get("dangling_entra_detail"):
            item["evidence"] = _trim(d.get("evidence") or d.get("dangling_entra_detail"), 180)
        # Minimal dns/http signal only
        dns = d.get("dns") or {}
        if dns.get("nxdomain"):
            item["dns"] = "nxdomain"
        elif dns.get("resolves"):
            item["dns"] = "ok"
        elif dns:
            item["dns"] = "fail"
        http = d.get("http") or d.get("http_probe") or {}
        if http.get("status") is not None:
            item["http_status"] = http["status"]
        elif http.get("error"):
            item["http_error"] = _trim(http["error"], 80)
        out.append(item)
    return out


def _compact_playwright(block: Any) -> Any:
    if not isinstance(block, dict):
        return block
    domains = []
    for d in block.get("domains") or []:
        if not isinstance(d, dict):
            continue
        domains.append(
            {
                "domain": d.get("domain"),
                "confidence": d.get("confidence"),
                "evidence": _trim(d.get("evidence"), 100),
                "sources": list(d.get("sources") or [])[:2],
            }
        )
    return {
        "queries": block.get("queries"),
        "domains": domains,
        "hit_count": block.get("hit_count") or len(block.get("hits") or []),
        "error": block.get("error"),
    }


def _compact_dnslytics(block: Any) -> Any:
    if not isinstance(block, dict):
        return block
    return {
        "seed_domains": block.get("seed_domains"),
        "tags": block.get("tags"),
        "domains": [
            {
                "domain": d.get("domain"),
                "gtm_tags": d.get("gtm_tags"),
                "confidence": d.get("confidence"),
                "evidence": _trim(d.get("evidence"), 100),
            }
            for d in (block.get("domains") or [])
            if isinstance(d, dict)
        ],
        "errors": list(block.get("errors") or [])[:10],
        "error": block.get("error"),
    }


def _compact_urlscan(block: Any) -> Any:
    if not isinstance(block, dict):
        return block
    return {
        "queries": block.get("queries"),
        "result_count": block.get("result_count"),
        "domains": [
            {
                "domain": d.get("domain"),
                "relation": d.get("relation"),
                "sources": list(d.get("sources") or [])[:2],
            }
            for d in (block.get("domains") or [])
            if isinstance(d, dict)
        ],
        "errors": list(block.get("errors") or [])[:10],
    }


def compact_report(report: dict[str, Any]) -> dict[str, Any]:
    """Return a size-optimized copy of a pipeline report."""
    parent = dict(report.get("parent") or {})
    # Drop bulky EDGAR candidate lists from ambiguous matches in compact output
    if parent.get("candidates") and len(parent["candidates"]) > 5:
        parent["candidates"] = parent["candidates"][:5]

    domains = [
        _compact_domain_row(d)
        for d in (report.get("domains") or [])
        if isinstance(d, dict) and d.get("domain")
    ]
    # Prefer enriched / verified / dangling — drop empty stubs
    domains = [
        d
        for d in domains
        if (d.get("discovery") or {}).get("relationship_verified")
        or d.get("dangling_entra")
        or d.get("rdap")
        or d.get("azure_tenant")
        or d.get("asn")
        or d.get("is_parked")
    ]

    entities = []
    for e in report.get("entities") or []:
        if not isinstance(e, dict):
            continue
        entities.append(
            {
                "name": e.get("name"),
                "type": e.get("type"),
                "confidence": e.get("confidence"),
                "relationship_verified": bool(e.get("relationship_verified")),
                "evidence": _trim(e.get("evidence"), 120),
                "sources": list(e.get("sources") or [])[:2],
                "domains": [
                    x.get("domain") if isinstance(x, dict) else x
                    for x in (e.get("domains") or [])
                ][:5],
            }
        )

    out: dict[str, Any] = {
        "query": report.get("query"),
        "generated_at": report.get("generated_at"),
        "parent": parent,
        "entities": entities,
        "domains": domains,
        "urlscan": _compact_urlscan(report.get("urlscan")) if report.get("urlscan") else None,
        "playwright": _compact_playwright(report.get("playwright"))
        if report.get("playwright")
        else None,
        "dnslytics": _compact_dnslytics(report.get("dnslytics"))
        if report.get("dnslytics")
        else None,
        "tenant_domains": _compact_tenant_domains(report.get("tenant_domains"))
        if report.get("tenant_domains")
        else None,
        "entra_verify": (
            {
                "expected_tenant_id": (report.get("entra_verify") or {}).get(
                    "expected_tenant_id"
                ),
                "counts": (report.get("entra_verify") or {}).get("counts"),
                "matched": (report.get("entra_verify") or {}).get("matched"),
                "mismatched": (report.get("entra_verify") or {}).get("mismatched"),
                "error": (report.get("entra_verify") or {}).get("error"),
            }
            if report.get("entra_verify")
            else None
        ),
        "dangling_check": report.get("dangling_check"),
        "dangling_domains": _compact_dangling(report.get("dangling_domains") or []),
        "notes": list(report.get("notes") or [])[:20],
        "errors": list(report.get("errors") or [])[:30],
    }
    return out


def ensure_summary(report: dict[str, Any]) -> dict[str, Any]:
    """
    Guarantee ``summary`` + ``summary_text`` exist and are placed first.
    Safe for full reports and single-plugin wrappers.
    """
    if not isinstance(report, dict):
        report = {"result": report}

    try:
        summary = report.get("summary")
        if not isinstance(summary, dict):
            summary = build_summary(report)
        # Required fields — always coerce falsy company
        company = (
            summary.get("company")
            or (report.get("parent") or {}).get("legal_name")
            or (report.get("parent") or {}).get("name")
            or report.get("query")
            or report.get("company")
            or report.get("plugin")
            or "unknown"
        )
        summary["company"] = company
        summary.setdefault("domains", [])
        summary.setdefault("entities", [])
        summary.setdefault(
            "counts",
            {
                "domains": len(summary.get("domains") or []),
                "entities": len(summary.get("entities") or []),
                "dangling": len(report.get("dangling_domains") or []),
            },
        )
        summary_text = report.get("summary_text") or format_summary_text(summary)
    except Exception as e:  # noqa: BLE001
        summary = {
            "company": report.get("query") or "unknown",
            "domains": [],
            "entities": [],
            "counts": {"domains": 0, "entities": 0, "dangling": 0},
            "error": f"summary build failed: {e}",
        }
        summary_text = f"Company: {summary['company']}\n(summary unavailable)"

    # Rebuild with summary first
    ordered: dict[str, Any] = {
        "summary": summary,
        "summary_text": summary_text,
    }
    for k, v in report.items():
        if k in {"summary", "summary_text"}:
            continue
        ordered[k] = v
    return ordered


def finalize_report(report: dict[str, Any], *, compact: bool = True) -> dict[str, Any]:
    """Compact (optional) then always attach summary."""
    body = compact_report(report) if compact else dict(report)
    # Build summary from compacted body so sources stay short
    body.pop("summary", None)
    body.pop("summary_text", None)
    return ensure_summary(body)
