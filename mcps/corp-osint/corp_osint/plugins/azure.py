"""Microsoft Entra (Azure AD) tenant lookup + OIDC domain verification."""

from __future__ import annotations

import asyncio
from typing import Any, Iterable, Optional

from .entra import (
    azure_tenant_from_oidc,
    fetch_oidc,
    fetch_user_realm,
    verify_domains_against_tenant,
)
from ..http import AsyncHttpClient
from ..plugin import Plugin
from ..util import normalize_domain


class AzureTenantLookup(Plugin):
    name = "azure_tenant"
    description = "OIDC / GetUserRealm Entra tenant lookup and domain verification"

    def __init__(self, http: AsyncHttpClient, config=None, **kwargs) -> None:
        super().__init__(http, config=config, **kwargs)

    async def run(self, **kwargs: Any) -> Any:
        # Batch verify mode
        if kwargs.get("expected_tenant_id") and (
            kwargs.get("domains") is not None or kwargs.get("domain")
        ):
            domains = list(kwargs.get("domains") or [])
            if kwargs.get("domain"):
                domains.insert(0, str(kwargs["domain"]))
            return await self.verify_domains(
                domains,
                str(kwargs["expected_tenant_id"]),
                max_domains=int(kwargs.get("max_domains") or 200),
                concurrency=int(kwargs.get("concurrency") or 10),
            )

        domain = kwargs.pop("domain", None)
        if not domain:
            raise ValueError("domain required (or domains + expected_tenant_id)")
        return await self.enrich(str(domain), **kwargs)

    async def enrich(self, domain: str, **kwargs: Any) -> dict[str, Any]:
        domain = normalize_domain(domain) or domain
        assert self.http is not None
        self.log.info("progress [azure_tenant] lookup %s", domain)
        expected = (kwargs.get("expected_tenant_id") or "").lower().strip() or None
        oidc, realm = await asyncio.gather(
            fetch_oidc(self.http, domain),
            fetch_user_realm(self.http, domain),
        )
        return {
            "domain": domain,
            "azure_tenant": azure_tenant_from_oidc(
                oidc, realm=realm, expected_tenant_id=expected
            ),
        }

    async def verify_domains(
        self,
        domains: Iterable[str],
        expected_tenant_id: str,
        *,
        concurrency: int = 10,
        max_domains: int = 200,
        skip: Optional[set[str]] = None,
    ) -> dict[str, Any]:
        """OIDC-verify domains against a known company Entra tenant ID."""
        assert self.http is not None
        return await verify_domains_against_tenant(
            self.http,
            domains,
            expected_tenant_id,
            concurrency=concurrency,
            max_domains=max_domains,
            skip=skip,
        )
