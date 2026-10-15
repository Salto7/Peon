"""Shared small helpers (domain normalize, proxy URL parse)."""

from __future__ import annotations

from typing import Iterable, Optional
from urllib.parse import urlparse

# Common multi-part public suffixes (registrable = label + suffix)
_MULTI_TLD = frozenset(
    {
        "co.uk",
        "org.uk",
        "ac.uk",
        "gov.uk",
        "com.au",
        "net.au",
        "org.au",
        "co.jp",
        "or.jp",
        "ne.jp",
        "com.br",
        "com.ar",
        "com.mx",
        "co.il",
        "co.nz",
        "co.za",
        "com.cn",
        "com.tw",
        "com.hk",
        "com.sg",
        "co.in",
        "com.tr",
        "com.pt",
        "com.es",
    }
)


def normalize_domain(value: str | None) -> Optional[str]:
    """Lowercase host: strip, drop leading www., empty → None."""
    if not value:
        return None
    d = str(value).lower().strip().removeprefix("www.")
    return d or None


def apex_domain(value: str | None) -> Optional[str]:
    """Registrable / apex domain (e.g. ``app.example.com`` → ``example.com``)."""
    d = normalize_domain(value)
    if not d or "." not in d:
        return d
    parts = d.split(".")
    if len(parts) >= 3:
        suffix2 = ".".join(parts[-2:])
        if suffix2 in _MULTI_TLD:
            return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else d


def is_apex_domain(value: str | None) -> bool:
    """True when ``value`` is already a registrable apex (not a subdomain)."""
    d = normalize_domain(value)
    if not d or "." not in d:
        return False
    return d == apex_domain(d)


def dedupe_domains(values: Iterable[str | None], *, skip_onmicrosoft: bool = False) -> list[str]:
    """Normalize + dedupe domains, preserving first-seen order."""
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        d = normalize_domain(v)
        if not d or d in seen:
            continue
        if skip_onmicrosoft and d.endswith(".onmicrosoft.com"):
            continue
        seen.add(d)
        out.append(d)
    return out


def host_matches_no_proxy(host: str | None, no_proxy: Iterable[str]) -> bool:
    """Return True if ``host`` should bypass the configured proxy."""
    if not host or not no_proxy:
        return False
    h = host.lower().rstrip(".")
    for raw in no_proxy:
        rule = (raw or "").strip().lower()
        if not rule:
            continue
        if rule == "*":
            return True
        rule = rule.lstrip(".")
        if h == rule or h.endswith("." + rule):
            return True
    return False


def parse_proxy_url(url: str) -> dict[str, str]:
    """
    Parse ``http://``, ``https://``, ``socks5://``, ``socks4://`` proxy URLs.

    Returns a Playwright-compatible dict: server, optional username/password.
    """
    raw = url.strip()
    if "://" not in raw:
        raw = "http://" + raw
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "http").lower()
    if scheme not in {"http", "https", "socks4", "socks5", "socks5h"}:
        raise ValueError(f"Unsupported proxy scheme: {scheme!r}")
    # Playwright wants socks5 (not socks5h); aiohttp-socks accepts both
    server_scheme = "socks5" if scheme == "socks5h" else scheme
    host = parsed.hostname
    if not host:
        raise ValueError(f"Proxy URL missing host: {url!r}")
    port = parsed.port
    if port is None:
        port = {"http": 80, "https": 443, "socks4": 1080, "socks5": 1080}.get(server_scheme, 1080)
    out: dict[str, str] = {"server": f"{server_scheme}://{host}:{port}"}
    if parsed.username:
        out["username"] = parsed.username
    if parsed.password:
        out["password"] = parsed.password
    return out
