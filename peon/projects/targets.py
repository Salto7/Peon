"""Engagement subjects: open producer types, no Peon type inference.

A node is ``{type, value}`` — ``type`` is whatever the producer declared.
Identity is the exact label (``value``); same string merges into one node.
The only structural contract is ``service`` → ``host:port[/protocol]``.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from orchestrator.utils.paths import format_target_lines

# ── tokens / line parsing ──────────────────────────────────────────────────

_NON_SLUG = re.compile(r"[^a-z0-9_]+")
_TYPE_PREFIX = re.compile(
    r"^(?P<type>[a-z_][a-z0-9_]{0,31})\s*:\s*(?P<value>.+)$",
    re.I,
)
_SERVICE_RE = re.compile(
    r"^(?:\[(?P<bracket_host>[^\]\s]+)\]|(?P<host>[^:/\s]+))"
    r":(?P<port>\d{1,5})(?:/(?P<protocol>[a-z][a-z0-9_-]{0,15}))?$",
    re.I,
)
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}\.?$)"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*\.?$",
    re.I,
)
_ASSET_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.&'-]{0,63}$")
_QUOTED_ASSET_RE = re.compile(
    r"""['\"“”‘’`]\s*([A-Za-z][A-Za-z0-9 .&'-]{1,60})\s*['\"“”‘’`]"""
)
_SENTENCE_PUNCT_RE = re.compile(r"[.?!:;]")

_STOP = frozenset(
    {
        "example.com",
        "example.org",
        "example.net",
        "localhost",
        "github.com",
        "openrouter.ai",
    }
)
_NON_SUBJECT_TYPES = frozenset(
    {
        "phase",
        "description",
        "project",
        "title",
        "acceptance",
        "acceptance_criteria",
        "role",
        "role_id",
        "skill",
        "skill_ids",
        "objective",
        "approach",
        "goal",
        "depends_on",
        "mitre",
        "commands",
        "status",
        "seq",
    }
)
_ASSET_NAME_TYPES = frozenset(
    {"asset", "organization", "organisation", "company", "org", "vendor", "name"}
)

def sanitize_label(raw: str | None, *, default: str = "", max_len: int = 32) -> str:
    """Lowercase slug: ``[a-z][a-z0-9_]{0,max_len-1}``. Empty → default."""
    s = _NON_SLUG.sub("_", (raw or "").strip().lower()).strip("_")
    if not s:
        return default[:max_len] if default else ""
    if not s[0].isalpha():
        s = f"x_{s}"
    return s[:max_len]


def split_typed_line(text: str) -> tuple[str, str] | None:
    """Parse ``type:value`` if the type token is a valid slug; else None.

    URI schemes (``https://…``, ``git://…``) are not producer prefixes — the
    value would start with ``//``.
    """
    match = _TYPE_PREFIX.match((text or "").strip())
    if not match:
        return None
    typ = sanitize_label(match.group("type"))
    value = match.group("value").strip()
    if not typ or not value:
        return None
    if value.startswith("//"):
        return None
    return typ, value


def is_subject_type(typ: str) -> bool:
    """False for planner/objective metadata labels that must not authorize scope."""
    return sanitize_label(typ) not in _NON_SUBJECT_TYPES


# ── IP / net helpers (scope coverage — not type inference) ─────────────────



def normalize_ip(value: str) -> str | None:
    """Canonical IP string, or None if not a full address."""
    raw = (value or "").strip()
    if not raw or "/" in raw:
        return None
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return None


def _as_network(value: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        if "/" in raw:
            return ipaddress.ip_network(raw, strict=False)
        return ipaddress.ip_network(f"{ipaddress.ip_address(raw)}/32", strict=False)
    except ValueError:
        return None


def target_authorized_by_scope(target: str, scope_values: Iterable[str]) -> bool:
    """True when ``target`` equals / is covered by an authorized scope entry."""
    needle = (target or "").strip().lower()
    if not needle:
        return False
    scope = [str(s or "").strip().lower() for s in scope_values if str(s or "").strip()]
    if not scope:
        return False
    if any(needle == s or needle in s or s in needle for s in scope):
        return True
    want = _as_network(needle)
    if want is None:
        return False
    for s in scope:
        auth = _as_network(s)
        if auth is None or want.version != auth.version:
            continue
        try:
            if want.subnet_of(auth) or want == auth:
                return True
        except (TypeError, ValueError):
            continue
    return False


def url_host(raw: str) -> str:
    """Hostname from a URL-ish string, or empty on failure."""
    try:
        return (urlparse(raw).hostname or "").strip()
    except Exception:
        return ""


def hostname_of(value: str) -> str:
    """Hostname from a URL or bare host string."""
    raw = (value or "").strip()
    if not raw:
        return ""
    host = url_host(raw if "://" in raw else f"https://{raw}")
    return (host or "").lower().rstrip(".")


def is_passive_reference_host(host: str) -> bool:
    """True for hosts an MCP server declares as reference infrastructure."""
    h = (host or "").strip().lower().rstrip(".")
    if not h:
        return False
    from peon.projects.platform_catalog import PlatformCatalog

    refs = PlatformCatalog.shared().reference_hosts()
    return h in refs or any(h.endswith("." + ref) for ref in refs)


# ── service contract (only when producer type is already ``service``) ──────


@dataclass(frozen=True)
class ServiceEndpoint:
    """Canonical service asset: ``host:port[/protocol]``."""

    host: str
    port: int
    protocol: str = ""

    @property
    def value(self) -> str:
        rendered_host = f"[{self.host}]" if ":" in self.host else self.host
        base = f"{rendered_host}:{self.port}"
        return f"{base}/{self.protocol}" if self.protocol else base

    @property
    def identity(self) -> str:
        """Stable key fragment: host:port (protocol is a property, not identity)."""
        return f"{self.host}:{self.port}"


def _normalize_host_token(raw_host: str, *, bracketed: bool = False) -> str | None:
    host = normalize_ip(raw_host)
    if host is not None:
        return host
    if bracketed or not _HOSTNAME_RE.fullmatch(raw_host):
        return None
    return raw_host.lower().rstrip(".")


def parse_service(value: str) -> ServiceEndpoint | None:
    """Parse the service contract. Anything else → None (no free-text salvage)."""
    raw = (value or "").strip().rstrip(".,;")
    match = _SERVICE_RE.fullmatch(raw)
    if not match:
        return None
    port = int(match.group("port"))
    if not 1 <= port <= 65535:
        return None
    raw_host = match.group("bracket_host") or match.group("host") or ""
    host = _normalize_host_token(raw_host, bracketed=bool(match.group("bracket_host")))
    if host is None:
        return None
    protocol = (match.group("protocol") or "").lower()
    return ServiceEndpoint(host=host, port=port, protocol=protocol)


def format_service(host: str, port: int | str, protocol: str = "") -> str | None:
    """Build a canonical service value from structured fields (the only composer)."""
    normalized_host = _normalize_host_token((host or "").strip())
    if normalized_host is None:
        return None
    try:
        port_i = int(port)
    except (TypeError, ValueError):
        return None
    if not 1 <= port_i <= 65535:
        return None
    protocol_s = sanitize_label(protocol, default="", max_len=16)
    return ServiceEndpoint(normalized_host, port_i, protocol_s).value


def resolve_service(
    value: str,
    *,
    host: str = "",
    port: int | str | None = None,
    protocol: str = "",
) -> str | None:
    """Resolve a service value from contract text and/or structured parts."""
    endpoint = parse_service(value)
    if endpoint is not None:
        return endpoint.value
    digit = (value or "").strip()
    if digit.isdigit() and host:
        return format_service(host, digit, protocol)
    if host and port is not None:
        return format_service(host, port, protocol)
    return None


def _clean_value(value: str) -> str | None:
    v = (value or "").strip().rstrip(".,;")
    if not v:
        return None
    if v.lower() in _STOP:
        return None
    if re.search(r"[\x00-\x1f]", v):
        return None
    return v[:1024]


# ── accept / identity (producer type is authoritative) ─────────────────────


def accept_asset(value: str, typ: str = "") -> dict[str, str] | None:
    """Accept one producer asset. Type is never inferred from the value.

    - Any non-empty producer type + cleaned value → stored as-is.
    - ``service`` alone requires the ``host:port[/proto]`` contract.
    - Missing type → ``other`` (opaque); producers should type explicitly.
    """
    v = _clean_value(value)
    if not v:
        return None
    hint = sanitize_label(typ)
    if hint == "service":
        endpoint = parse_service(v)
        return {"type": "service", "value": endpoint.value} if endpoint else None
    if hint:
        return {"type": hint, "value": v}
    return {"type": "other", "value": v}


def canonical_asset_key(typ: str, value: str) -> str:
    """Stable identity = exact label (value), independent of producer type.

    Same display string → same node (``ip`` + ``host`` for one address merge).
    For ``service``, the label collapses to ``host:port`` so protocol variants
    of the same socket share one node.
    """
    accepted = accept_asset(value, typ)
    if accepted is None:
        clean = (value or "").strip().lower()
        family = sanitize_label(typ, default="other") or "other"
    else:
        clean = accepted["value"].lower()
        family = accepted["type"]
    if family == "service":
        endpoint = parse_service(clean)
        if endpoint is not None:
            clean = endpoint.identity
    return clean[:400]


# ── asset names (org / brand) ──────────────────────────────────────────────


def _asset_tokens(value: str) -> list[str] | None:
    cleaned = _clean_value(value)
    if not cleaned:
        return None
    if "@" in cleaned or "/" in cleaned or "://" in cleaned or "," in cleaned:
        return None
    if _SENTENCE_PUNCT_RE.search(cleaned):
        return None
    words = [w for w in cleaned.split() if w]
    if not words or len(words) > 3:
        return None
    if not all(_ASSET_TOKEN_RE.fullmatch(w) for w in words):
        return None
    return words


def _is_proper_name_tokens(words: list[str]) -> bool:
    """Structural proper-name form (Title Case / ALL CAPS) — no word lists."""
    if not words:
        return False
    if len(words) == 1:
        token = words[0]
        if token.isupper() and token.isalpha() and len(token) >= 2:
            return True
        return token[0].isupper() and not token.islower()
    return all(w[0].isupper() for w in words)


def as_asset_name(value: str, *, explicit: bool = False) -> dict[str, str] | None:
    """Accept a bare asset name (1–3 tokens, proper-name form unless explicit)."""
    words = _asset_tokens(value)
    if not words:
        return None
    if explicit:
        if len(words) == 1 or _is_proper_name_tokens(words):
            return {"type": "asset", "value": " ".join(words)}
        return None
    if not _is_proper_name_tokens(words):
        return None
    return {"type": "asset", "value": " ".join(words)}


# ── coerce ─────────────────────────────────────────────────────────────────


def coerce_target(raw: Any) -> dict[str, str] | None:
    """Normalize one target to ``{type, value, notes?}``. Never invents types."""
    if raw is None:
        return None

    if isinstance(raw, dict):
        value = str(raw.get("value") or raw.get("target") or "").strip()
        if not value:
            return None
        typ = sanitize_label(
            str(raw.get("type") or raw.get("asset_type") or ""),
            default="",
        )
        if typ and not is_subject_type(typ):
            return None
        if typ in _ASSET_NAME_TYPES:
            return as_asset_name(value, explicit=True)
        accepted = accept_asset(value, typ)
        if accepted is None:
            return None
        notes = str(raw.get("notes") or "").strip()
        out = dict(accepted)
        if notes:
            out["notes"] = notes[:500]
        return out

    text = str(raw).strip()
    if not text:
        return None
    split = split_typed_line(text)
    if split is not None:
        typ, value = split
        if not is_subject_type(typ):
            return None
        if typ in _ASSET_NAME_TYPES:
            return as_asset_name(value, explicit=True)
        return accept_asset(value, typ)
    named = as_asset_name(text)
    if named:
        return named
    return accept_asset(text, "other")


def prefer_producer_type(previous: str, new: str) -> str:
    """Prefer a concrete producer type over empty/other. First concrete wins."""
    if previous in {"", "other"} and new not in {"", "other"}:
        return new
    if previous not in {"", "other"}:
        return previous
    return new or previous or "other"


def prefer_by_rank(
    previous: str,
    new: str,
    ranks: dict[str, int],
    *,
    default: str = "",
) -> str:
    """Keep the higher-ranked label; on a tie prefer ``new``."""
    if ranks.get(new, 0) >= ranks.get(previous, 0):
        return new or previous or default
    return previous or new or default


def merge_typed_rows(
    rows: Iterable[dict[str, str]],
    *,
    on_dup: Any = None,
) -> list[dict[str, str]]:
    """De-dupe ``{type,value,…}`` rows by ``canonical_asset_key``.

    On collision, merge types via ``prefer_producer_type`` and optionally run
    ``on_dup(prev, new)`` for field-specific merges (notes, relation, …).
    """
    by_key: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for row in rows:
        if not row or not str(row.get("value") or "").strip():
            continue
        key = canonical_asset_key(row.get("type", ""), row["value"])
        prev = by_key.get(key)
        if prev is None:
            by_key[key] = dict(row)
            order.append(key)
            continue
        prev["type"] = prefer_producer_type(prev.get("type", ""), row.get("type", ""))
        if on_dup is not None:
            on_dup(prev, row)
        elif row.get("notes") and not prev.get("notes"):
            prev["notes"] = row["notes"]
    return [by_key[k] for k in order]


def coerce_targets(raw: Iterable[Any] | None) -> list[dict[str, str]]:
    """Normalize targets and de-duplicate by exact label (value)."""
    return merge_typed_rows(
        target for item in (raw or []) if (target := coerce_target(item)) is not None
    )


def format_targets(raw: Iterable[Any] | None) -> list[str]:
    return format_target_lines(coerce_targets(raw))


def extract_targets(*texts: str) -> list[dict[str, str]]:
    """Typed lines and quoted/proper names from free text.

    Does not scrape URLs/IPs/emails into invented types — producers must type
    subjects (``ip:…``, ``url:…``, …) or pass structured ``{type,value}``.
    """
    blob = "\n".join(t for t in texts if t)
    found: list[dict[str, str]] = []

    for line in blob.splitlines():
        item = line.strip()
        if not item:
            continue
        split = split_typed_line(item)
        if split is not None:
            typ, value = split
            if not is_subject_type(typ):
                continue
            if typ in _ASSET_NAME_TYPES:
                named = as_asset_name(value, explicit=True)
                if named:
                    found.append(named)
                continue
            accepted = accept_asset(value, typ)
            if accepted:
                found.append(accepted)
            continue
        named = as_asset_name(item)
        if named:
            found.append(named)

    for match in _QUOTED_ASSET_RE.finditer(blob):
        named = as_asset_name(match.group(1), explicit=True)
        if named:
            found.append(named)

    return coerce_targets(found)


def parse_target_lines(raw: str) -> list[dict[str, str]]:
    """Parse textarea lines: ``type:value`` or bare values (as ``other``)."""
    parts = [
        item
        for line in (raw or "").replace(",", "\n").splitlines()
        if (item := line.strip())
    ]
    return coerce_targets(parts)
