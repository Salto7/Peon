"""Shared parking / for-sale page detection."""

from __future__ import annotations

import re
from typing import Optional

PARKING_MARKERS: tuple[str, ...] = (
    "domain for sale",
    "buy this domain",
    "this domain is for sale",
    "this domain may be for sale",
    "domain is for sale",
    "is for sale",
    "parked free",
    "parked domain",
    "parkingcrew",
    "sedoparking",
    "sedo.com",
    "afternic.com",
    "hugedomains.com",
    "dan.com/buy-domain",
    "dan.com",
    "godaddy.com/forsale",
    "forsale.godaddy",
    "domainmarket.com",
    "squadhelp.com",
    "brandpa.com",
    "above.com",
    "bodis.com",
    "parked-content.godaddy",
    "related searches",
    "buy domains",
    "this webpage is parked",
    "the domain has expired",
    "domain has been registered",
    "renew this domain",
    "get this domain",
    "purchase this domain",
)

_TITLE_PARKED = re.compile(
    r"(?i)\b(for sale|parked|buy this domain|domain for sale|coming soon)\b"
)


def detect_parking(
    *,
    title: str = "",
    text: str = "",
    html: str = "",
    final_url: str = "",
) -> tuple[bool, Optional[str]]:
    """Return (is_parked, matched_marker)."""
    blob = " ".join([title or "", text or "", html[:8000] if html else "", final_url or ""]).lower()
    if not blob.strip():
        return False, None
    for marker in PARKING_MARKERS:
        if marker in blob:
            return True, marker
    if title and _TITLE_PARKED.search(title):
        return True, f"title:{title.strip()[:80]}"
    return False, None
