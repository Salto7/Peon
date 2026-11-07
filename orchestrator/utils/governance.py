"""Platform-level safety/governance primitives shared across enforcement points.

Centralizes the mechanisms every denial path relies on:

- a fixed framing prefix so an agent can tell "the platform blocked this" from
  "the target blocked this" (and isn't nudged into treating platform policy
  as something to evade, the way it would treat a target's WAF);
- a hard, non-disableable scope blocklist checked ahead of any per-project
  Rules of Engagement. This is a module constant, not a DB row or project
  setting, so no UI/API path can turn it off;
- SSRF/internal-network resolution for any outbound request whose host is
  operator- or third-party-supplied (a remote MCP server's url, a reference
  host) — resolve, then classify every resolved address, never just the
  literal hostname string.
"""

from __future__ import annotations

import ipaddress
import socket

PLATFORM_DENIAL_PREFIX = "[Peon platform control, not target defense]"


def frame_denial(reason: str) -> str:
    """Prefix a platform-origin denial so it reads as governance, not a target block.

    Idempotent — framing an already-framed reason (e.g. one bubbled up through
    another denial path) leaves it unchanged rather than double-prefixing.
    """
    text = (reason or "").strip()
    if not text or text.startswith(PLATFORM_DENIAL_PREFIX):
        return text
    return f"{PLATFORM_DENIAL_PREFIX} {text}"


# Deliberately a module constant: extend only by editing this file, never via
# Settings/RoE/catalog, so no project configuration can disable it. Bare TLDs
# plus their common ccTLD forms (a government/military/education host rarely
# sits under the bare TLD outside the US) — most of the real world otherwise
# sails through a bare-suffix-only check.
HARD_SCOPE_BLOCKLIST_SUFFIXES: frozenset[str] = frozenset(
    {
        ".gov",
        ".mil",
        ".edu",
        ".int",
        ".gov.uk",
        ".gov.au",
        ".gov.za",
        ".gov.in",
        ".gob.mx",
        ".gob.ar",
        ".gouv.fr",
        ".gouv.qc.ca",
        ".go.jp",
        ".go.kr",
        ".go.th",
        ".go.id",
        ".gc.ca",
        ".gov.br",
        ".mil.br",
        ".mil.za",
        ".ac.uk",
        ".edu.au",
        ".ac.jp",
        ".edu.cn",
        ".ac.in",
        ".edu.sg",
    }
)


def hard_blocklist_reason(hostname: str) -> str:
    """Non-empty denial reason when ``hostname`` matches the fixed platform blocklist."""
    host = (hostname or "").strip().lower().rstrip(".")
    if not host:
        return ""
    for suffix in HARD_SCOPE_BLOCKLIST_SUFFIXES:
        bare = suffix.lstrip(".")
        if host == bare or host.endswith(suffix):
            return (
                f"{host!r} matches the platform's hard scope blocklist ({suffix}) — "
                "this cannot be overridden by project RoE"
            )
    return ""


# Carrier-grade NAT (RFC 6598) — not covered by ipaddress.IPv4Address.is_private
# on CPython as of 3.12, unlike the RFC1918 ranges it does cover.
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _is_internal_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or (ip.version == 4 and ip in _CGNAT)
    )


def resolve_blocks_internal_network(hostname: str) -> str:
    """Non-empty denial reason when ``hostname`` resolves to an internal/reserved address.

    For any host Peon itself will connect to on an operator's or a third
    party's say-so (a remote MCP server's ``url``, a reference host) — never
    just string-matching the hostname literal (``169.254.169.254.evil.com``
    is not ``169.254.``-prefixed; a bare RFC1918 IP typed as the hostname
    isn't ``localhost`` either). Resolves every address the name returns and
    blocks if *any* of them is internal, so a multi-A-record host can't hide
    one real answer behind a public-looking one.

    Fails closed: a name that doesn't resolve at all is blocked too — the
    alternative (letting an unresolvable name through) is exactly the gap a
    DNS-rebinding attack exploits, where the name resolves differently at
    connect time than it did when this was last checked.
    """
    host = (hostname or "").strip().rstrip(".")
    if not host:
        return "empty hostname"
    try:
        ipaddress.ip_address(host)
        addrs = [host]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError as exc:
            return f"{host!r} could not be resolved ({exc}); refusing to connect"
        addrs = sorted({info[4][0] for info in infos})
    for addr in addrs:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return f"{host!r} resolved to an unparseable address {addr!r}"
        if _is_internal_address(ip):
            return f"{host!r} resolves to {addr!r}, an internal/reserved address"
    return ""


UNVERIFIED_LESSON_PREFIX = "[Unverified prior lesson — may not apply to this engagement]"


def frame_recalled_lesson(text: str) -> str:
    """Prefix a memory-recalled lesson (Core 6.1) so an agent treats it as a
    hint to weigh against this engagement's own evidence, not as fact —
    the RSI-safety counterpart to ``frame_denial``: a prior "it worked last
    time" claim is not this target's ground truth. Idempotent.
    """
    body = (text or "").strip()
    if not body or body.startswith(UNVERIFIED_LESSON_PREFIX):
        return body
    return f"{UNVERIFIED_LESSON_PREFIX} {body}"


__all__ = [
    "PLATFORM_DENIAL_PREFIX",
    "frame_denial",
    "hard_blocklist_reason",
    "resolve_blocks_internal_network",
    "frame_recalled_lesson",
]
