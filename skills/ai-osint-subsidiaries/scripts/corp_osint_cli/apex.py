"""Registrable-domain helpers (stdlib, no PSL)."""
from __future__ import annotations

import re

_DOMAIN_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$",
    re.I,
)
_CC_LEFT = frozenset(
    {
        "de",
        "uk",
        "fr",
        "es",
        "it",
        "nl",
        "au",
        "ca",
        "jp",
        "br",
        "mx",
        "in",
        "cn",
        "us",
        "ie",
        "pl",
        "se",
        "ch",
        "at",
        "be",
        "pt",
        "cz",
        "dk",
        "fi",
        "no",
        "kr",
        "tw",
        "hk",
        "sg",
        "my",
        "nz",
        "za",
        "ae",
        "tr",
        "ar",
        "cl",
        "co",
        "pe",
        "ru",
    }
)
_PUBLISHER_SUFFIXES = frozenset(
    {
        "wikipedia.org",
        "wikidata.org",
        "wikimedia.org",
        "wikiwand.com",
        "sec.gov",
        "linkedin.com",
        "facebook.com",
        "instagram.com",
        "twitter.com",
        "x.com",
        "youtube.com",
        "youtu.be",
        "reddit.com",
        "medium.com",
        "blogspot.com",
        "wordpress.com",
        "google.com",
        "bing.com",
        "duckduckgo.com",
    }
)


def norm_host(raw: str) -> str:
    d = (raw or "").strip().lower()
    d = re.sub(r"^https?://", "", d)
    d = d.split("/")[0].split(":")[0].split("?")[0]
    d = d.removeprefix("www.").strip(".")
    return d


def is_domain(host: str) -> bool:
    return bool(host and _DOMAIN_RE.match(host))


def is_publisher_host(host: str) -> bool:
    """True for encyclopedia / social / search hosts that are not company sites."""
    h = (host or "").strip(".").lower()
    if not h:
        return False
    for suffix in _PUBLISHER_SUFFIXES:
        if h == suffix or h.endswith("." + suffix):
            return True
    return False


def registrable_apex(raw: str) -> str:
    """Prefer eTLD+1-ish host: de.acme.com → acme.com.

    Publisher hosts (Wikipedia, LinkedIn, SEC EDGAR, …) are never company domains.
    """
    host = norm_host(raw)
    if not is_domain(host) or is_publisher_host(host):
        return ""
    parts = host.split(".")
    if len(parts) >= 3 and parts[0] in _CC_LEFT:
        candidate = ".".join(parts[1:])
        if is_domain(candidate) and not is_publisher_host(candidate):
            return candidate
    return host


def prefer_shorter_apex(current: str, incoming: str) -> str:
    a = registrable_apex(current)
    b = registrable_apex(incoming)
    if not a:
        return b
    if not b:
        return a
    if a.endswith("." + b) or b.endswith("." + a):
        return a if len(a) <= len(b) else b
    return a
