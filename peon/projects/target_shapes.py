"""Target shape normalization and coercion for RoE / findings."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlparse

from orchestrator.utils.paths import format_target_lines

_NON_SLUG = re.compile(r"[^a-z0-9_]+")
_TYPE_PREFIX = re.compile(
    r"^(?P<type>[a-z_][a-z0-9_]{0,31})\s*:\s*(?P<value>.+)$",
    re.I,
)

_EMAIL_RE = re.compile(r"\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b", re.I)
_URL_RE = re.compile(
    r"(?:https?|ftps?|wss?|git)://[^\s,;\)\]\"'<>]+",
    re.I,
)
_IP_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|[01]?\d?\d)(?:/\d{1,2})?\b"
)
_ASN_RE = re.compile(r"^AS(\d{1,10})$", re.I)
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
# Digest lengths only (md5/sha1/sha256/sha512) — standards, not file extensions.
_HASH_RE = re.compile(r"^(?:[a-f0-9]{32}|[a-f0-9]{40}|[a-f0-9]{64}|[a-f0-9]{128})$", re.I)
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

def sanitize_label(raw: str | None, *, default: str = "", max_len: int = 32) -> str:
    """Lowercase slug: ``[a-z][a-z0-9_]{0,max_len-1}``. Empty → default."""
    s = _NON_SLUG.sub("_", (raw or "").strip().lower()).strip("_")
    if not s:
        return default[:max_len] if default else ""
    if not s[0].isalpha():
        s = f"x_{s}"
    return s[:max_len]


def split_typed_line(text: str) -> tuple[str, str] | None:
    """Parse ``type:value`` if the type token is a valid slug; else None."""
    match = _TYPE_PREFIX.match((text or "").strip())
    if not match:
        return None
    typ = sanitize_label(match.group("type"))
    value = match.group("value").strip()
    if not typ or not value:
        return None
    return typ, value


def normalize_netblock(value: str) -> str | None:
    """Canonical CIDR (``192.168.10.1/24`` → ``192.168.10.0/24``)."""
    raw = (value or "").strip()
    if not raw or "/" not in raw:
        return None
    try:
        return str(ipaddress.ip_network(raw, strict=False))
    except ValueError:
        return None


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
    """True when ``target`` equals / is covered by an authorized scope entry.

    CIDR/IP: a narrower netblock or host inside an authorized supernet is allowed
    (``192.168.10.64/26`` under ``192.168.10.0/24``). Hostnames/URLs keep
    exact/substring match for non-IP targets.
    """
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
        if auth is None:
            continue
        try:
            if want.version != auth.version:
                continue
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


@dataclass(frozen=True)
class ServiceEndpoint:
    """Parsed structured service asset (``host:port[/protocol]``)."""

    host: str
    port: int
    protocol: str = ""

    @property
    def value(self) -> str:
        rendered_host = f"[{self.host}]" if ":" in self.host else self.host
        base = f"{rendered_host}:{self.port}"
        return f"{base}/{self.protocol}" if self.protocol else base


def parse_service(value: str) -> ServiceEndpoint | None:
    """Parse and normalize the service asset contract."""
    raw = (value or "").strip().rstrip(".,;")
    match = _SERVICE_RE.fullmatch(raw)
    if not match:
        return None
    port = int(match.group("port"))
    if not 1 <= port <= 65535:
        return None
    raw_host = match.group("bracket_host") or match.group("host") or ""
    host = normalize_ip(raw_host)
    if host is None:
        if match.group("bracket_host") or not _HOSTNAME_RE.fullmatch(raw_host):
            return None
        host = raw_host.lower().rstrip(".")
    protocol = (match.group("protocol") or "").lower()
    return ServiceEndpoint(host=host, port=port, protocol=protocol)


def format_service(host: str, port: int, protocol: str = "") -> str | None:
    """Build the canonical service value from structured fields."""
    raw_host = (host or "").strip()
    normalized_host = normalize_ip(raw_host)
    if normalized_host is None:
        if not _HOSTNAME_RE.fullmatch(raw_host):
            return None
        normalized_host = raw_host.lower().rstrip(".")
    try:
        port_i = int(port)
    except (TypeError, ValueError):
        return None
    if not 1 <= port_i <= 65535:
        return None
    protocol_s = sanitize_label(protocol, default="", max_len=16)
    return ServiceEndpoint(normalized_host, port_i, protocol_s).value


def detect_shape(value: str) -> str:
    """Standards shape of a bare value: ip|netblock|url|email|asn|service|hash|''."""
    v = (value or "").strip().rstrip(".,;")
    if not v:
        return ""
    if _URL_RE.match(v):
        return "url"
    if _EMAIL_RE.fullmatch(v):
        return "email"
    if _ASN_RE.fullmatch(v):
        return "asn"
    if _HASH_RE.fullmatch(v):
        return "hash"
    if normalize_netblock(v):
        return "netblock"
    if normalize_ip(v):
        return "ip"
    if parse_service(v):
        return "service"
    return ""


def _clean_value(value: str) -> str | None:
    v = (value or "").strip().rstrip(".,;")
    if not v or len(v) < 1:
        return None
    if v.lower() in _STOP:
        return None
    if re.search(r"[\x00-\x1f]", v):
        return None
    return v[:1024]


def accept_asset(value: str, typ: str = "") -> dict[str, str] | None:
    """Accept and canonicalize a producer asset.

    Intrinsic shapes are authoritative; producer types remain available for
    values without an unambiguous standards shape.
    """
    v = _clean_value(value)
    if not v:
        return None
    hint = sanitize_label(typ)

    # Standards normalize when the value is that shape (regardless of hint).
    shape = detect_shape(v)
    if shape == "netblock":
        canon = normalize_netblock(v)
        if canon:
            return {"type": "netblock", "value": canon}
    if shape == "ip":
        canon = normalize_ip(v) or v.split("/")[0]
        return {"type": "ip", "value": canon[:128]}
    if shape == "url":
        return {"type": "url", "value": v}
    if shape == "email":
        return {"type": "email", "value": v[:320]}
    if shape == "asn":
        m = _ASN_RE.fullmatch(v)
        return {"type": "asn", "value": f"AS{m.group(1)}" if m else v}
    if shape == "service":
        endpoint = parse_service(v)
        if endpoint:
            return {"type": "service", "value": endpoint.value}
    if shape == "hash":
        return {"type": "hash", "value": v.lower()}

    # Producer supplied a type — trust it (Amass: type comes from the plugin).
    if hint:
        return {"type": hint, "value": v}

    return None


def canonical_asset_key(typ: str, value: str) -> str:
    """Stable identity shared by RoE, findings, and the graph."""
    accepted = accept_asset(value, typ)
    if accepted is None:
        family = sanitize_label(typ, default="other") or "other"
        clean = (value or "").strip().lower()
    else:
        family = accepted["type"]
        clean = accepted["value"].lower()
    if family in {"host", "fqdn", "domain", "hostname"}:
        family = "host"
    return f"{family}:{clean}"[:400]


def coerce_target(raw: Any) -> dict[str, str] | None:
    """Normalize one target to ``{type, value, notes?}``. Trusts producer type."""
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
        accepted = accept_asset(value, typ)
        if accepted is None:
            # Dict producers always keep the row (authorization by value).
            cleaned = _clean_value(value)
            if not cleaned:
                return None
            accepted = {"type": typ or "other", "value": cleaned}
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
        return accept_asset(split[1], split[0]) or {
            "type": split[0],
            "value": split[1][:1024],
        }
    accepted = accept_asset(text)
    if accepted:
        return accepted
    # Bare string with no standards shape — keep as other (seed/brief text).
    cleaned = _clean_value(text)
    if not cleaned:
        return None
    return {"type": "other", "value": cleaned}


def coerce_targets(raw: Iterable[Any] | None) -> list[dict[str, str]]:
    """Normalize targets and de-duplicate by canonical identity."""
    by_value: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for item in raw or []:
        target = coerce_target(item)
        if target is None:
            continue
        key = canonical_asset_key(target.get("type", ""), target["value"])
        prev = by_value.get(key)
        if prev is None:
            by_value[key] = target
            order.append(key)
            continue
        # Prefer non-other producer type; otherwise keep first.
        if prev.get("type") in {"", "other"} and target.get("type") not in {"", "other"}:
            by_value[key] = target
    return [by_value[k] for k in order]


def format_targets(raw: Iterable[Any] | None) -> list[str]:

    return format_target_lines(coerce_targets(raw))


def extract_targets(*texts: str) -> list[dict[str, str]]:
    """Standards shapes + ``type:value`` lines only (no hostname/filename scrape)."""
    blob = "\n".join(t for t in texts if t)
    found: list[dict[str, str]] = []

    for line in blob.replace(",", "\n").splitlines():
        item = line.strip()
        if not item:
            continue
        split = split_typed_line(item)
        if split is not None:
            accepted = accept_asset(split[1], split[0])
            if accepted:
                found.append(accepted)
            else:
                cleaned = _clean_value(split[1])
                if cleaned:
                    found.append({"type": split[0], "value": cleaned})

    def _add(typ: str, value: str) -> None:
        accepted = accept_asset(value, typ)
        if accepted:
            found.append(accepted)

    for match in _URL_RE.finditer(blob):
        _add("url", match.group(0))
    for match in _EMAIL_RE.finditer(blob):
        _add("email", match.group(0))
    for match in _IP_RE.finditer(blob):
        raw = match.group(0)
        _add("netblock" if "/" in raw else "ip", raw)

    return coerce_targets(found)

def parse_target_lines(raw: str) -> list[dict[str, str]]:
    """Parse textarea lines: ``type:value`` or bare values."""
    parts: list[str] = []
    for line in (raw or "").replace(",", "\n").splitlines():
        item = line.strip()
        if item:
            parts.append(item)
    return coerce_targets(parts)
