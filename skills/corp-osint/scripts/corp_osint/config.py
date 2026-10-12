"""Runtime configuration and throttle defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


def _split_no_proxy(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(p.strip() for p in raw.replace(";", ",").split(",") if p.strip())


def resolve_proxy_url(explicit: str | None = None) -> Optional[str]:
    """CLI/env proxy resolution. Empty string disables env proxy."""
    if explicit is not None:
        return explicit.strip() or None
    for key in (
        "CORP_OSINT_PROXY",
        "ALL_PROXY",
        "all_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
    ):
        val = os.environ.get(key)
        if val and val.strip():
            return val.strip()
    return None


@dataclass
class ThrottleConfig:
    """Per-host concurrency and minimum spacing between requests."""

    max_concurrent: int = 6
    min_interval_sec: float = 0.08
    # Host-specific overrides (host substring → (concurrent, min_interval))
    host_limits: dict[str, tuple[int, float]] = field(
        default_factory=lambda: {
            "sec.gov": (2, 0.5),
            "data.sec.gov": (2, 0.5),
            "urlscan.io": (1, 0.75),
            "a.dnslytics.com": (1, 3.5),
            "search.dnslytics.com": (1, 3.5),
            "api.dnslytics.net": (1, 2.0),
            "login.microsoftonline.com": (3, 0.15),
            "rdap.org": (3, 0.2),
            "stat.ripe.net": (2, 0.3),
            "api.bgpview.io": (1, 0.5),
            "func-idefix-tenantlookup.azurewebsites.net": (2, 0.35),
            "autodiscover-s.outlook.com": (2, 0.4),
            "bing.com": (1, 1.0),
        }
    )


@dataclass
class HttpConfig:
    user_agent: str = field(
        default_factory=lambda: os.environ.get(
            "CORP_OSINT_USER_AGENT",
            os.environ.get(
                "SEC_USER_AGENT",
                "CorpOSINT/2.0 (research; contact@acme.com)",
            ),
        )
    )
    timeout_sec: float = 30.0
    retries: int = 6
    retry_min_wait: float = 1.0
    retry_max_wait: float = 45.0
    # http://, https://, socks5://, socks4:// — also CORP_OSINT_PROXY / *_PROXY env
    proxy: str | None = field(default_factory=resolve_proxy_url)
    no_proxy: tuple[str, ...] = field(
        default_factory=lambda: _split_no_proxy(
            os.environ.get("CORP_OSINT_NO_PROXY")
            or os.environ.get("NO_PROXY")
            or os.environ.get("no_proxy")
        )
    )

    def playwright_proxy(self) -> dict[str, str] | None:
        """Playwright ``proxy=`` dict, or None when unset."""
        if not self.proxy:
            return None
        from .util import parse_proxy_url

        return parse_proxy_url(self.proxy)


@dataclass
class PipelineConfig:
    company: str
    cik: str | None = None
    ticker: str | None = None
    domain: str | None = None
    max_entities: int = 12
    max_enrich_domains: int = 6
    resolve_sites: bool = True
    use_edgar: bool = True
    use_urlscan: bool = True
    use_playwright: bool = True
    use_asn: bool = True
    use_dnslytics: bool = True
    use_tenant_domains: bool = True  # idefixtools.com tenant → domains
    entra_verify_domains: bool = True  # OIDC-verify found domains against company tenant
    entra_verify_max: int = 200
    entra_verify_concurrency: int = 10
    check_dangling_parked: bool = True  # probe Entra domains for parked/dead/unregistered
    dangling_max_domains: int = 200  # cap Entra domain probes
    dangling_concurrency: int = 12
    dangling_check_rdap: bool = True  # RDAP for NXDOMAIN / no-DNS cases
    asn_deep: bool = False  # sample prefixes (slow); default = observed IPs only
    playwright_engine: str = "bing"
    playwright_max_queries: int = 6
    urlscan_size: int = 30
    dnslytics_max_domains: int = 5
    dnslytics_max_gtm_reverse: int = 5
    tenant_domains_max: int = 150
    compact_report: bool = True  # strip WHOIS dumps / slim dangling JSON
    http: HttpConfig = field(default_factory=HttpConfig)
    throttle: ThrottleConfig = field(default_factory=ThrottleConfig)

    @property
    def dnslytics_api_key(self) -> str | None:
        return os.environ.get("DNSLYTICS_API_KEY") or os.environ.get("DNSLYTICS_APIKEY")

    @property
    def urlscan_api_key(self) -> str | None:
        return os.environ.get("URLSCAN_API_KEY") or os.environ.get("URLSCAN_API_TOKEN")
