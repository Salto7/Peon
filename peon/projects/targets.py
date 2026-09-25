"""Engagement assets for RoE / findings (Amass-inspired, producer-trust).

Shape: ``{type, value, notes?}``. **Value authorizes**; ``type`` is a free-form
slug from the producer (skill / finding / operator / LLM) — not a closed enum.

Peon does **not** run a central type oracle. It:
- sanitizes slug/value length
- normalizes standards shapes (IP, CIDR via ``ipaddress``, URL host, email)
- accepts structured producer output as-is
- extracts from free text only standards shapes + ``type:value`` lines
  (optional LLM as another producer)
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any, Iterable
from urllib.parse import urlparse

from peon.projects.models import Project, RulesOfEngagement


_NON_SLUG = re.compile(r"[^a-z0-9_]+")
_TYPE_PREFIX = re.compile(
    r"^(?P<type>[a-z_][a-z0-9_]{0,31})\s*:\s*(?P<value>.+)$",
    re.I,
)

# Skills that actively probe remotes — need non-empty in_scope *values*
# (type labels are hints only; never gate on type).
ACTIVE_NETWORK_SKILLS = frozenset(
    {
        "network-scanner",
        "http-prober",
    }
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
    r"^((?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)"
    r"|(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63})"
    r":(\d{1,5})$",
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

_LLM_ASSET_SYSTEM = """You extract engagement subjects for Peon (authorized analysis).
Return JSON only: {"assets":[{"type":"<slug>","value":"<string>"}]}
type is a free-form lowercase slug from the operator's wording (examples: ip, fqdn,
url, netblock, file, malware, sample, source, repo, package, hash, email,
organization, person). Only include subjects clearly named. No speculation.
If none, return {"assets":[]}."""


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


def url_host(raw: str) -> str:
    """Hostname from a URL-ish string, or empty on failure."""
    try:
        return (urlparse(raw).hostname or "").strip()
    except Exception:
        return ""


def detect_shape(value: str) -> str:
    """Standards shape of a bare value: ip|netblock|url|email|asn|service|hash|''."""
    v = (value or "").strip().rstrip(".,;:")
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
    svc = _SERVICE_RE.fullmatch(v)
    if svc:
        port = int(svc.group(2))
        if 1 <= port <= 65535:
            return "service"
    return ""


def _clean_value(value: str) -> str | None:
    v = (value or "").strip().rstrip(".,;:")
    if not v or len(v) < 1:
        return None
    if v.lower() in _STOP:
        return None
    if re.search(r"[\x00-\x1f]", v):
        return None
    return v[:1024]


def accept_asset(value: str, typ: str = "") -> dict[str, str] | None:
    """Accept a producer asset: trust ``typ`` when set; else standards shape only.

    This replaces a central type oracle. Unknown producer types are kept.
    Untyped free text is only accepted when IP / CIDR / URL / email / ASN /
    service / hash is unambiguous.
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
            return {"type": hint or "netblock", "value": canon}
    if shape == "ip":
        canon = normalize_ip(v) or v.split("/")[0]
        return {"type": hint or "ip", "value": canon[:128]}
    if shape == "url":
        return {"type": hint or "url", "value": v}
    if shape == "email":
        return {"type": hint or "email", "value": v[:320]}
    if shape == "asn":
        m = _ASN_RE.fullmatch(v)
        return {"type": hint or "asn", "value": f"AS{m.group(1)}" if m else v}
    if shape == "service":
        m = _SERVICE_RE.fullmatch(v)
        if m:
            return {
                "type": hint or "service",
                "value": f"{m.group(1).lower()}:{int(m.group(2))}",
            }
    if shape == "hash":
        return {"type": hint or "hash", "value": v.lower()}

    # Producer supplied a type — trust it (Amass: type comes from the plugin).
    if hint:
        return {"type": hint, "value": v}

    return None


def expand_related_assets(asset: dict[str, str]) -> list[dict[str, str]]:
    """Lightweight fan-out: URL → host; service → host (graph tips, not taxonomy)."""
    typ = asset.get("type") or ""
    value = asset.get("value") or ""
    out = [asset]
    shape = detect_shape(value)
    if typ == "url" or shape == "url":
        host = url_host(value)
        if host:
            related = accept_asset(host, "host")
            if related:
                out.append(related)
    elif typ == "service" or shape == "service":
        if ":" in value:
            host = value.rsplit(":", 1)[0]
            related = accept_asset(host) or accept_asset(host, "host")
            if related:
                out.append(related)
    return out


def discovery_assets_from_finding(row: dict[str, Any] | None) -> list[dict[str, str]]:
    """Candidates from structured finding fields — producer provenance, not scrape.

    Sources: host/port, evidence_path, asset_type, metadata asset lists / subjects.
    """
    if not isinstance(row, dict):
        return []
    found: list[dict[str, str]] = []

    def _push(typ: str, value: str) -> None:
        accepted = accept_asset(value, typ)
        if not accepted:
            return
        for asset in expand_related_assets(accepted):
            found.append(asset)

    host = str(row.get("host") or "").strip()
    asset_type = sanitize_label(str(row.get("asset_type") or ""), default="")
    port = row.get("port")
    if host:
        _push(asset_type or "host", host)
        try:
            port_i = int(port) if port is not None and str(port).strip() != "" else None
        except (TypeError, ValueError):
            port_i = None
        if port_i is not None and 1 <= port_i <= 65535:
            _push("service", f"{host}:{port_i}")

    # Field name is provenance; asset_type is the producer label when present.
    for key, default_typ in (
        ("evidence_path", "file"),
        ("url", "url"),
        ("uri", "url"),
        ("repo", "repo"),
        ("hash", "hash"),
        ("sample", "sample"),
        ("malware", "malware"),
        ("file", "file"),
        ("source", "source"),
        ("package", "package"),
    ):
        raw = row.get(key)
        if raw:
            hint = asset_type if key in {"evidence_path", "file", "sample"} and asset_type else default_typ
            _push(hint, str(raw))

    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    for key in (
        "assets",
        "discovered_assets",
        "candidates",
        "targets",
        "urls",
        "uris",
        "files",
        "hashes",
        "samples",
    ):
        raw = meta.get(key)
        if isinstance(raw, str):
            default = (
                "hash"
                if key == "hashes"
                else "file"
                if key in {"files", "samples"}
                else "url"
                if key in {"urls", "uris"}
                else asset_type
            )
            _push(default, raw)
            continue
        if not isinstance(raw, list):
            continue
        for item in raw:
            if isinstance(item, str):
                default = (
                    "hash"
                    if key == "hashes"
                    else "file"
                    if key in {"files", "samples"}
                    else "url"
                    if key in {"urls", "uris"}
                    else asset_type
                )
                _push(default, item)
                continue
            coerced = coerce_target(item)
            if coerced:
                _push(str(coerced.get("type") or ""), str(coerced.get("value") or ""))

    for key, default_typ in (
        ("hash", "hash"),
        ("sha256", "hash"),
        ("md5", "hash"),
        ("path", "file"),
        ("file", "file"),
        ("sample", "sample"),
        ("malware", "malware"),
        ("repo", "repo"),
        ("subject", asset_type or "other"),
    ):
        raw = meta.get(key)
        if raw:
            _push(default_typ if default_typ != "other" else asset_type or "other", str(raw))

    return coerce_targets(found)


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
    """Normalize targets; one entry per value. Prefer explicit type over ``other``."""
    by_value: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for item in raw or []:
        target = coerce_target(item)
        if target is None:
            continue
        key = target["value"].lower()
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
    out: list[str] = []
    for t in coerce_targets(raw):
        typ = t.get("type") or "other"
        value = t.get("value") or ""
        out.append(value if typ == "other" else f"{typ}:{value}")
    return out


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


def roe_block_reason(skill_names: Iterable[str] | None, scope: Iterable[Any] | None) -> str | None:
    """Fail-closed when active probe skills have no in-scope *values*."""
    names = [str(n).strip() for n in (skill_names or []) if str(n).strip()]
    if coerce_targets(scope):
        return None
    needing = [n for n in names if n in ACTIVE_NETWORK_SKILLS]
    if not needing:
        return None
    return (
        "Rules of Engagement fail-closed: in_scope has no authorized targets for "
        f"{', '.join(needing)} (add targets or deduce from brief)"
    )


def parse_target_lines(raw: str) -> list[dict[str, str]]:
    """Parse textarea lines: ``type:value`` or bare values."""
    parts: list[str] = []
    for line in (raw or "").replace(",", "\n").splitlines():
        item = line.strip()
        if item:
            parts.append(item)
    return coerce_targets(parts)


def provision_project_roe(
    project: Project,
    *,
    texts: Iterable[str] | None = None,
    exclusions: list | None = None,
    authorization: str = "",
) -> tuple[RulesOfEngagement, list[str]]:
    """
    Ensure RoE exists. If in_scope is empty, deduce assets from texts /
    title+summary into in_scope. Type labels are soft producer hints.
    """
    roe, _ = RulesOfEngagement.objects.get_or_create(project=project)
    deduced: list[str] = []
    scope = coerce_targets(roe.in_scope)
    if not scope:
        parts = list(texts or [])
        parts.extend([project.title or "", project.summary or ""])
        assets = extract_targets(*parts)
        if assets:
            roe.in_scope = assets
            deduced = [t["value"] for t in assets]
            if authorization and not roe.authorization_note:
                roe.authorization_note = authorization
            elif not roe.authorization_note:
                roe.authorization_note = (
                    "Auto-deduced from brief (operator did not supply Rules of Engagement)."
                )
            if not coerce_targets(getattr(roe, "seed", None)):
                seed_bits = [
                    s.strip()
                    for s in (project.title, project.summary)
                    if (s or "").strip()
                ]
                if seed_bits:
                    roe.seed = coerce_targets(
                        [{"type": "other", "value": " | ".join(seed_bits)[:500]}]
                    )
            roe.save()
            scope = assets
    elif scope != list(roe.in_scope or []):
        roe.in_scope = scope
        roe.save(update_fields=["in_scope", "updated_at"])

    if exclusions is not None:
        roe.exclusions = coerce_targets(exclusions)
        roe.save(update_fields=["exclusions", "updated_at"])
    if authorization and not roe.authorization_note:
        roe.authorization_note = authorization
        roe.save(update_fields=["authorization_note", "updated_at"])
    try:
        project.roe = roe
    except Exception:
        pass
    return roe, deduced


def promote_candidates(
    roe: RulesOfEngagement,
    *,
    indices: list[int] | None = None,
    all_candidates: bool = False,
) -> int:
    """Move selected (or all) candidates into in_scope. Returns count promoted."""
    candidates = coerce_targets(roe.candidates)
    if not candidates:
        return 0
    if all_candidates:
        chosen = candidates
        remaining: list[dict[str, str]] = []
    else:
        wanted = set(indices or [])
        chosen = [c for i, c in enumerate(candidates) if i in wanted]
        remaining = [c for i, c in enumerate(candidates) if i not in wanted]
    if not chosen:
        return 0
    scope = coerce_targets(roe.in_scope)
    scope.extend(chosen)
    roe.in_scope = coerce_targets(scope)
    roe.candidates = remaining
    roe.save(update_fields=["in_scope", "candidates", "updated_at"])
    return len(chosen)


def add_candidates(roe: RulesOfEngagement, raw: Iterable) -> int:
    """Append discovered assets as candidates (not yet authorized)."""
    incoming = coerce_targets(raw)
    if not incoming:
        return 0
    existing = coerce_targets(roe.candidates)
    before = len(existing)
    existing.extend(incoming)
    roe.candidates = coerce_targets(existing)
    roe.save(update_fields=["candidates", "updated_at"])
    try:
        from peon.projects.asset_graph import ingest_assets

        project = getattr(roe, "project", None)
        if project is not None:
            ingest_assets(project, incoming, bucket="candidate", source="rules_of_engagement")
    except Exception:
        pass
    return len(roe.candidates) - before


def _llm_suggest_assets(text: str) -> list[dict[str, str]]:
    """LLM as a discovery producer — returns typed assets; peon trusts the types."""
    try:
        from orchestrator.utils.llm import chat_json, llm_configured

        if not llm_configured():
            return []
        data = chat_json(_LLM_ASSET_SYSTEM, (text or "")[:4000])
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("assets")
    if not isinstance(raw, list):
        return []
    found: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        coerced = coerce_target(item)
        if coerced and coerced.get("value"):
            found.append(coerced)
    return coerce_targets(found)


def authorize_operator_scope(roe: RulesOfEngagement, text: str) -> list[dict[str, str]]:
    """Merge operator-named assets into RoE in_scope (chat/replan = authorization)."""
    blob = text or ""
    incoming = extract_targets(blob)
    if not incoming and len(blob.strip()) >= 8:
        incoming = _llm_suggest_assets(blob)
    if not incoming:
        return []
    known = {
        t["value"].lower()
        for t in coerce_targets(roe.in_scope) + coerce_targets(roe.exclusions)
        if t.get("value")
    }
    novel = [t for t in incoming if t.get("value", "").lower() not in known]
    if not novel:
        return []
    scope = coerce_targets(roe.in_scope)
    scope.extend(novel)
    roe.in_scope = coerce_targets(scope)
    cands = [
        c
        for c in coerce_targets(roe.candidates)
        if c.get("value", "").lower() not in {n["value"].lower() for n in novel}
    ]
    roe.candidates = cands
    roe.save(update_fields=["in_scope", "candidates", "updated_at"])
    return novel


def roe_add_path(project: Project, sandbox_path: str) -> None:
    """Authorize a project-local input path in RoE seed + in_scope."""
    roe, _ = RulesOfEngagement.objects.get_or_create(project=project)
    path = (sandbox_path or "").strip()
    if not path:
        return
    seed = coerce_targets(roe.seed)
    scope = coerce_targets(roe.in_scope)
    row = {"type": "file", "value": path}
    if not any(t.get("value") == path for t in seed):
        seed.append(row)
    if not any(t.get("value") == path for t in scope):
        scope.append(row)
    roe.seed = seed
    roe.in_scope = scope
    roe.save(update_fields=["seed", "in_scope", "updated_at"])


def roe_remove_path(project: Project, sandbox_path: str) -> None:
    """Drop a path value from RoE seed + in_scope (if present)."""
    roe = getattr(project, "roe", None)
    if roe is None:
        return
    path = (sandbox_path or "").strip()
    seed = [t for t in coerce_targets(roe.seed) if t.get("value") != path]
    scope = [t for t in coerce_targets(roe.in_scope) if t.get("value") != path]
    roe.seed = seed
    roe.in_scope = scope
    roe.save(update_fields=["seed", "in_scope", "updated_at"])
