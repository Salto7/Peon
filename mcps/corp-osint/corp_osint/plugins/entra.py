"""Shared Microsoft Entra / Azure AD OIDC helpers.

OIDC metadata:
  https://login.microsoftonline.com/{id}/v2.0/.well-known/openid-configuration

``id`` may be a domain or tenant GUID. The ``issuer`` field embeds the tenant GUID.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Iterable, Optional
from urllib.parse import quote

from ..http import AsyncHttpClient
from ..log import get_logger
from ..util import dedupe_domains, normalize_domain

log = get_logger("entra")

OIDC_URL = (
    "https://login.microsoftonline.com/{id}/v2.0/.well-known/openid-configuration"
)
REALM_URL = "https://login.microsoftonline.com/getuserrealm.srf?login={login}&json=1"

GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# Process-local OIDC cache (domain/guid → result) — avoids repeat fetches in one run
_oidc_cache: dict[str, dict[str, Any]] = {}


def oidc_url(identifier: str) -> str:
    return OIDC_URL.format(id=quote(identifier.strip(), safe=".-"))


def tenant_id_from_issuer(issuer: str | None) -> Optional[str]:
    if not issuer:
        return None
    for part in issuer.rstrip("/").split("/"):
        if GUID_RE.match(part):
            return part.lower()
    return None


def clear_oidc_cache() -> None:
    _oidc_cache.clear()


def _empty_oidc(ident: str, url: str, error: str | None) -> dict[str, Any]:
    return {
        "identifier": ident,
        "tenant_id": None,
        "issuer": None,
        "tenant_region_scope": None,
        "oidc_url": url,
        "error": error,
    }


async def fetch_oidc(
    http: AsyncHttpClient,
    identifier: str,
    *,
    timeout: float | None = 15.0,
    use_cache: bool = True,
) -> dict[str, Any]:
    """
    Resolve Entra tenant metadata for a domain or tenant GUID via OIDC.

    Returns keys: tenant_id, issuer, tenant_region_scope, oidc_url, error.
    """
    ident = normalize_domain(identifier) or str(identifier).lower().strip()
    if use_cache and ident in _oidc_cache:
        return dict(_oidc_cache[ident])

    url = oidc_url(ident)
    try:
        status, body, _ = await http.get(
            url,
            headers={"Accept": "application/json"},
            timeout=timeout,
            attempts=2,
            retry_on_client_error=True,
        )
    except Exception as e:  # noqa: BLE001
        return _empty_oidc(ident, url, str(e))

    if status == 404:
        out = _empty_oidc(ident, url, "OIDC metadata not found")
    elif status >= 400:
        out = _empty_oidc(ident, url, f"HTTP {status}")
    else:
        doc = json.loads(body.decode("utf-8", errors="replace"))
        issuer = doc.get("issuer") or ""
        tenant_id = tenant_id_from_issuer(issuer)
        if not tenant_id and GUID_RE.match(ident):
            tenant_id = ident.lower()
        out = {
            "identifier": ident,
            "tenant_id": tenant_id,
            "issuer": issuer or None,
            "tenant_region_scope": doc.get("tenant_region_scope"),
            "oidc_url": url,
            "error": None if tenant_id else "Issuer did not contain a tenant GUID",
        }

    if use_cache:
        _oidc_cache[ident] = out
    return dict(out)


async def fetch_user_realm(http: AsyncHttpClient, domain: str) -> dict[str, Any]:
    """Optional GetUserRealm enrichment (brand / federation type)."""
    domain = normalize_domain(domain) or domain
    url = REALM_URL.format(login=quote(f"user@{domain}", safe="@."))
    try:
        status, body, _ = await http.get(
            url,
            headers={"Accept": "application/json"},
            timeout=12.0,
            attempts=2,
        )
    except Exception:  # noqa: BLE001
        return {}
    if status >= 400:
        return {}
    doc = json.loads(body.decode("utf-8", errors="replace"))
    return {
        k: doc[k]
        for k in (
            "NameSpaceType",
            "FederationBrandName",
            "CloudInstanceName",
            "DomainName",
            "IsFederatedNs",
        )
        if doc.get(k) is not None
    }


def azure_tenant_from_oidc(
    oidc: dict[str, Any],
    *,
    realm: dict[str, Any] | None = None,
    expected_tenant_id: str | None = None,
) -> dict[str, Any]:
    """Build the standard ``azure_tenant`` report block from OIDC (+ optional realm)."""
    tenant_id = oidc.get("tenant_id")
    expected = (expected_tenant_id or "").lower().strip() or None
    az: dict[str, Any] = {
        "tenant_found": bool(tenant_id),
        "tenant_id": tenant_id,
        "issuer": oidc.get("issuer"),
        "tenant_region_scope": oidc.get("tenant_region_scope"),
        "oidc_url": oidc.get("oidc_url"),
        "user_realm": realm or {},
        "error": oidc.get("error"),
    }
    if expected:
        az["expected_tenant_id"] = expected
        az["matches_company_tenant"] = bool(tenant_id and tenant_id == expected)
    return az


async def verify_domains_against_tenant(
    http: AsyncHttpClient,
    domains: Iterable[str],
    expected_tenant_id: str,
    *,
    concurrency: int = 10,
    max_domains: int = 200,
    skip: Optional[set[str]] = None,
) -> dict[str, Any]:
    """
    OIDC-check domains and mark which resolve to ``expected_tenant_id``.

    Skips ``.onmicrosoft.com`` and any domain in ``skip`` (already known).
    """
    expected = expected_tenant_id.lower().strip()
    skip_set = {normalize_domain(s) or s for s in (skip or set()) if s}
    ordered = [
        d
        for d in dedupe_domains(domains, skip_onmicrosoft=True)
        if d not in skip_set
    ][: max(0, max_domains)]

    log.info(
        "OIDC-verify %d domain(s) against tenant=%s (concurrency=%d)",
        len(ordered),
        expected,
        concurrency,
    )

    sem = asyncio.Semaphore(concurrency)
    results: list[dict[str, Any]] = []
    matched: list[str] = []
    mismatched: list[dict[str, str]] = []
    not_entra: list[str] = []

    async def _one(dom: str) -> None:
        async with sem:
            meta = await fetch_oidc(http, dom)
            tid = meta.get("tenant_id")
            row = {
                "domain": dom,
                "tenant_id": tid,
                "matches_company_tenant": bool(tid and tid == expected),
                "oidc_url": meta.get("oidc_url"),
                "issuer": meta.get("issuer"),
                "error": meta.get("error"),
            }
            results.append(row)
            if row["matches_company_tenant"]:
                matched.append(dom)
                log.debug("entra match %s", dom)
            elif tid:
                mismatched.append({"domain": dom, "tenant_id": tid})
                log.debug("entra mismatch %s → %s", dom, tid)
            else:
                not_entra.append(dom)
                log.debug("no entra %s (%s)", dom, meta.get("error"))

    if ordered:
        await asyncio.gather(*[_one(d) for d in ordered])
    results.sort(key=lambda r: (0 if r["matches_company_tenant"] else 1, r["domain"]))

    log.info(
        "OIDC-verify done matched=%d mismatched=%d not_entra=%d",
        len(matched),
        len(mismatched),
        len(not_entra),
    )
    return {
        "expected_tenant_id": expected,
        "checked": len(results),
        "matched": matched,
        "mismatched": mismatched,
        "not_entra": not_entra,
        "results": results,
        "counts": {
            "checked": len(results),
            "matched": len(matched),
            "mismatched": len(mismatched),
            "not_entra": len(not_entra),
        },
    }
