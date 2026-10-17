"""Runtime configuration and throttle defaults."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field, fields


def _split_no_proxy(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(p.strip() for p in raw.replace(";", ",").split(",") if p.strip())


def resolve_proxy_url(explicit: str | None = None) -> str | None:
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
            "a.dnslytics.com": (1, 3.0),
            "search.dnslytics.com": (1, 3.0),
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
                "CorpOSINT/2.0 (research; contact@example.com)",
            ),
        )
    )
    timeout_sec: float = 30.0
    retries: int = 6
    retry_min_wait: float = 0.2  # 200ms floor; see http.RATE_LIMIT_STEP_SEC
    retry_max_wait: float = 3.0  # hard cap; see http.RATE_LIMIT_MAX_SEC
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


# Public stage names for --skip / --only (CLI + MCP).
STAGE_ALIASES: dict[str, str] = {
    "edgar": "edgar",
    "urlscan": "urlscan",
    "playwright": "playwright",
    "playwright_search": "playwright",
    "asn": "asn",
    "dnslytics": "dnslytics",
    "dnslytics_gtm": "dnslytics",
    "tenant_domains": "tenant_domains",
    "entra_verify": "entra_verify",
    "dangling": "dangling",
    "dangling_check": "dangling",
    "resolve": "resolve_sites",
    "resolve_sites": "resolve_sites",
}

STAGE_NAMES: tuple[str, ...] = (
    "edgar",
    "urlscan",
    "playwright",
    "asn",
    "dnslytics",
    "tenant_domains",
    "entra_verify",
    "dangling",
    "resolve_sites",
)


@dataclass
class StageConfig:
    """Which pipeline stages run. All enabled by default."""

    edgar: bool = True
    urlscan: bool = True
    playwright: bool = True
    asn: bool = True
    dnslytics: bool = True
    tenant_domains: bool = True
    entra_verify: bool = True
    dangling: bool = True
    resolve_sites: bool = True


@dataclass
class LimitsConfig:
    """Internal caps and tuning (not exposed on the slim CLI)."""

    max_entities: int = 12
    max_enrich_domains: int = 6
    # None = no limit. These bound *work*; they must never silently drop
    # discovered domains from the result.
    entra_verify_max: int | None = None
    entra_verify_concurrency: int = 10
    dangling_max_domains: int | None = None
    dangling_concurrency: int = 12
    dangling_check_rdap: bool = True
    urlscan_size: int = 30
    dnslytics_max_domains: int = 5
    dnslytics_max_gtm_reverse: int = 5
    tenant_domains_max: int | None = None
    playwright_max_queries: int = 6


def _normalize_stage_token(raw: str) -> str:
    key = raw.strip().lower().replace("-", "_")
    if not key:
        raise ValueError("empty stage name")
    if key not in STAGE_ALIASES:
        known = ", ".join(STAGE_NAMES)
        raise ValueError(f"unknown stage {raw!r}; expected one of: {known}")
    return STAGE_ALIASES[key]


def parse_stage_list(raw: str | Iterable[str] | None) -> list[str]:
    """Parse comma-separated or iterable stage names into canonical names."""
    if raw is None:
        return []
    if isinstance(raw, str):
        tokens = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
    else:
        tokens = [str(p).strip() for p in raw if str(p).strip()]
    return [_normalize_stage_token(t) for t in tokens]


def apply_stage_selection(
    stages: StageConfig,
    *,
    skip: str | Iterable[str] | None = None,
    only: str | Iterable[str] | None = None,
) -> StageConfig:
    """Apply ``skip`` and/or ``only`` to a StageConfig (mutates and returns it).

    ``only`` turns all stages off first, then enables the listed ones.
    ``skip`` disables listed stages (applied after ``only``).
    """
    only_names = parse_stage_list(only)
    skip_names = parse_stage_list(skip)
    if only_names:
        for f in fields(stages):
            setattr(stages, f.name, False)
        for name in only_names:
            setattr(stages, name, True)
    for name in skip_names:
        setattr(stages, name, False)
    return stages


@dataclass
class PipelineConfig:
    company: str
    cik: str | None = None
    ticker: str | None = None  # stock market ticker symbol
    domain: str | None = None
    stages: StageConfig = field(default_factory=StageConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    asn_deep: bool = False  # sample prefixes (slow); default = observed IPs only
    playwright_engine: str = "bing"
    compact_report: bool = True  # strip WHOIS dumps / slim dangling JSON
    http: HttpConfig = field(default_factory=HttpConfig)
    throttle: ThrottleConfig = field(default_factory=ThrottleConfig)

    @property
    def dnslytics_api_key(self) -> str | None:
        return os.environ.get("DNSLYTICS_API_KEY") or os.environ.get("DNSLYTICS_APIKEY")

    @property
    def urlscan_api_key(self) -> str | None:
        return os.environ.get("URLSCAN_API_KEY") or os.environ.get("URLSCAN_API_TOKEN")
