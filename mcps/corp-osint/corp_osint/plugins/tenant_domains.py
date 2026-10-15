"""Enumerate Entra-verified domains for a tenant (IdefixTools-compatible).

Uses the same public endpoints as https://idefixtools.com/tenant-lookup.html:
- Graph proxy:  GET https://func-idefix-tenantlookup.azurewebsites.net/api/lookup/{tenantId|domain}
- Domains API:  GET https://func-idefix-tenantlookup.azurewebsites.net/api/domains/{domain}
- Fallback:     Exchange Autodiscover GetFederationInformation
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import quote

from .entra import GUID_RE, fetch_oidc, oidc_url
from ..http import AsyncHttpClient
from ..plugin import Plugin
from ..util import normalize_domain

IDEFIX_LOOKUP = "https://func-idefix-tenantlookup.azurewebsites.net/api/lookup/{id}"
IDEFIX_DOMAINS = "https://func-idefix-tenantlookup.azurewebsites.net/api/domains/{domain}"
AUTODISCOVER = "https://autodiscover-s.outlook.com/autodiscover/autodiscover.svc"
SOURCE_UI = "https://idefixtools.com/tenant-lookup.html"

_FED_SOAP = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:exm="http://schemas.microsoft.com/exchange/services/2006/messages"
 xmlns:ext="http://schemas.microsoft.com/exchange/services/2006/types"
 xmlns:a="http://www.w3.org/2005/08/addressing"
 xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
 <soap:Header>
  <a:Action soap:mustUnderstand="1">http://schemas.microsoft.com/exchange/2010/Autodiscover/Autodiscover/GetFederationInformation</a:Action>
  <a:To soap:mustUnderstand="1">https://autodiscover-s.outlook.com/autodiscover/autodiscover.svc</a:To>
  <a:ReplyTo><a:Address>http://www.w3.org/2005/08/addressing/anonymous</a:Address></a:ReplyTo>
 </soap:Header>
 <soap:Body>
  <GetFederationInformationRequestMessage xmlns="http://schemas.microsoft.com/exchange/2010/Autodiscover">
   <Request><Domain>{domain}</Domain></Request>
  </GetFederationInformationRequestMessage>
 </soap:Body>
</soap:Envelope>"""


class TenantDomainLookup(Plugin):
    """Reverse tenant ID / seed domain → verified Entra domains."""

    name = "tenant_domains"

    def __init__(self, http: AsyncHttpClient, config=None, **kwargs) -> None:
        super().__init__(http, config=config, **kwargs)

    async def _oidc_tenant(self, identifier: str) -> dict[str, Any]:
        """Thin wrapper over shared ``fetch_oidc`` (Idefix field names)."""
        meta = await fetch_oidc(self.http, identifier)
        tid = meta.get("tenant_id")
        return {
            "tenantId": tid,
            "displayName": None,
            "defaultDomainName": None,
            "issuer": meta.get("issuer"),
            "tenant_region_scope": meta.get("tenant_region_scope"),
            "source": "oidc",
            "error": meta.get("error"),
        }

    async def lookup_tenant(self, tenant_or_domain: str) -> dict[str, Any]:
        tid = tenant_or_domain.strip()
        url = IDEFIX_LOOKUP.format(id=quote(tid, safe=".-"))
        status, body, _ = await self.http.get(
            url,
            headers={
                "Accept": "application/json",
                "Origin": "https://idefixtools.com",
                "Referer": SOURCE_UI,
            },
        )
        if status < 400:
            doc = json.loads(body.decode("utf-8", errors="replace"))
            return {
                "tenantId": (doc.get("tenantId") or "").lower() or None,
                "displayName": doc.get("displayName"),
                "defaultDomainName": (doc.get("defaultDomainName") or "").lower() or None,
                "federationBrandName": doc.get("federationBrandName"),
                "source": "idefix_lookup",
                "sources": [SOURCE_UI, url],
                "error": None,
            }

        # Idefix lookup often 400s on bare domains — fall back to shared OIDC
        oidc = await self._oidc_tenant(tid)
        if not oidc.get("error"):
            if not GUID_RE.match(tid):
                oidc["defaultDomainName"] = tid.lower()
            oidc["sources"] = [SOURCE_UI, oidc_url(tid)]
            return oidc
        return {
            "error": f"lookup HTTP {status}; {oidc.get('error')}",
            "tenantId": tid.lower() if GUID_RE.match(tid) else None,
        }

    async def fetch_domains_idefix(self, domain: str) -> list[str]:
        url = IDEFIX_DOMAINS.format(domain=quote(domain, safe=".-"))
        status, body, _ = await self.http.get(
            url,
            headers={
                "Accept": "application/json",
                "Origin": "https://idefixtools.com",
                "Referer": SOURCE_UI,
            },
            timeout=60.0,
        )
        if status >= 400:
            return []
        doc = json.loads(body.decode("utf-8", errors="replace"))
        return sorted({d for d in (normalize_domain(x) for x in (doc.get("domains") or []) if x) if d})

    async def fetch_domains_autodiscover(self, domain: str) -> list[str]:
        soap = _FED_SOAP.format(domain=domain)
        status, body, _ = await self.http.request(
            "POST",
            AUTODISCOVER,
            data=soap.encode("utf-8"),
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": (
                    '"http://schemas.microsoft.com/exchange/2010/Autodiscover/'
                    'Autodiscover/GetFederationInformation"'
                ),
            },
            timeout=45.0,
        )
        if status >= 400:
            return []
        text = body.decode("utf-8", errors="replace")
        # Regex only (avoid XML parsers / XXE on untrusted Autodiscover payloads)
        found = re.findall(r"<Domain>([^<]+)</Domain>", text, flags=re.I)
        return sorted({d for d in (normalize_domain(x) for x in found if x) if d})

    async def run(
        self,
        tenant_id: Optional[str] = None,
        domain: Optional[str] = None,
        max_domains: int = 200,
        **kwargs: Any,
    ) -> dict[str, Any]:
        errors: list[str] = []
        info: dict[str, Any] = {}
        seed = domain
        self.log.info("lookup tenant_id=%s domain=%s max=%d", tenant_id, domain, max_domains)

        if tenant_id:
            info = await self.lookup_tenant(tenant_id)
            if info.get("error"):
                errors.append(info["error"])
            seed = seed or info.get("defaultDomainName")
            tenant_id = (info.get("tenantId") or tenant_id).lower()
        elif domain:
            info = await self.lookup_tenant(domain)
            if info.get("error"):
                errors.append(info["error"])
            tenant_id = info.get("tenantId")
            seed = seed or info.get("defaultDomainName") or domain
        else:
            return {"error": "tenant_id or domain required", "domains": [], "errors": ["missing input"]}

        known: set[str] = set()
        if seed:
            known.add(seed.lower())
        if info.get("defaultDomainName"):
            known.add(info["defaultDomainName"])
            # well-known patterns from idefix JS
            base = info["defaultDomainName"].split(".onmicrosoft.com")[0]
            if base and ".onmicrosoft." in info["defaultDomainName"]:
                known.add(f"{base}.onmicrosoft.com")
                known.add(f"{base}.mail.onmicrosoft.com")

        # Probe custom domains via idefix domains API + Autodiscover
        probe = [d for d in known if not d.endswith(".onmicrosoft.com")]
        if domain and domain.lower() not in probe:
            probe.insert(0, domain.lower())
        if not probe and seed:
            probe = [seed.lower()]

        for d in probe[:5]:
            try:
                for name in await self.fetch_domains_idefix(d):
                    known.add(name)
            except Exception as e:  # noqa: BLE001
                errors.append(f"idefix domains {d}: {e}")
            try:
                for name in await self.fetch_domains_autodiscover(d):
                    known.add(name)
            except Exception as e:  # noqa: BLE001
                errors.append(f"autodiscover {d}: {e}")

        domains_sorted = sorted(
            known,
            key=lambda x: (x.endswith(".onmicrosoft.com"), x.endswith(".mail.onmicrosoft.com"), x),
        )[:max_domains]
        self.log.info(
            "found tenant=%s display=%s domains=%d errors=%d",
            tenant_id,
            info.get("displayName"),
            len(domains_sorted),
            len(errors),
        )
        if errors:
            self.log.warning("tenant_domains errors: %s", errors[:5])

        return {
            "tenant_id": tenant_id,
            "display_name": info.get("displayName"),
            "default_domain": info.get("defaultDomainName"),
            "federation_brand_name": info.get("federationBrandName"),
            "domains": [
                {
                    "domain": d,
                    "relation": "entra_verified",
                    "confidence": "high",
                    "evidence": "Entra tenant domain (IdefixTools / Autodiscover)",
                    "sources": [SOURCE_UI, IDEFIX_DOMAINS.format(domain=quote(probe[0] if probe else d, safe=".-"))],
                }
                for d in domains_sorted
            ],
            "domain_count": len(domains_sorted),
            "sources": [SOURCE_UI],
            "errors": errors,
        }
