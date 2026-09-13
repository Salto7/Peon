"""Asset-agnostic RoE / finding targets.

Shape: ``{type, value, notes?}``. **Value authorizes**; ``type`` is a soft hint
(operator/UI/planner preference). Seed / in-scope / candidates buckets matter
more than labels. Plain strings coerce on read/write.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from peon.projects.models import Project, RulesOfEngagement


_NON_SLUG = re.compile(r"[^a-z0-9_]+")
_TYPE_PREFIX = re.compile(
    r"^(?P<type>[a-z_][a-z0-9_]{0,31})\s*:\s*(?P<value>.+)$",
    re.I,
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


# Skills that actively probe remotes — need non-empty in_scope *values*
# (type labels are hints only; never gate on type).
ACTIVE_NETWORK_SKILLS = frozenset(
    {
        "network-scanner",
        "http-prober",
    }
)

# Soft preference ranks when collapsing duplicate values (hint only).
_TYPE_RANK = {
    "url": 10,
    "ip": 9,
    "host": 8,
    "domain": 7,
    "email": 6,
    "path": 5,
    "hash": 4,
    "phone": 3,
    "person": 2,
    "blob": 2,
    "other": 1,
}

_EMAIL_RE = re.compile(r"\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b", re.I)
# Dots are NOT phone separators — dotted decimals are IPv4 (partial or full).
_PHONE_RE = re.compile(
    r"(?<!\w)(?:\+?\d[\d\-()\s]{6,}\d)",
)
_HASH_RE = re.compile(r"\b(?:[a-f0-9]{32}|[a-f0-9]{40}|[a-f0-9]{64}|[a-f0-9]{128})\b", re.I)
_PATH_RE = re.compile(
    r"(?:(?:[a-zA-Z]:\\|/)[^\s,;\"']+\.(?:exe|dll|bin|so|dylib|apk|ipa|elf|sample|enc|stub))",
    re.I,
)
_URL_RE = re.compile(r"https?://[^\s,;\)\]\"']+", re.I)
_IP_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|[01]?\d?\d)(?:/\d{1,2})?\b"
)
# Incomplete IPv4 / prefix (e.g. 8.8.8) — never classify as phone.
_IPV4_PARTIAL_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){1,3}"
    r"(?:25[0-5]|2[0-4]\d|[01]?\d?\d)?(?:/\d{1,2})?\b"
)
_HOST_RE = re.compile(
    r"\b(?!(?:\d+\.)+\d+\b)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z]{2,63}\b",
    re.I,
)
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


def looks_like_ipv4(text: str) -> bool:
    """True for full or partial dotted IPv4 (optionally with CIDR)."""
    s = (text or "").strip().rstrip(".")
    if not s:
        return False
    return bool(_IP_RE.fullmatch(s) or _IPV4_PARTIAL_RE.fullmatch(s))


def refine_type(typ: str, value: str) -> str:
    """Prefer IPv4 over phone/other when the value is dotted-decimal."""
    if looks_like_ipv4(value):
        return "ip"
    return typ or "other"


def coerce_target(raw: Any) -> dict[str, str] | None:
    """Normalize one target to ``{type, value, notes}``."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        value = str(raw.get("value") or raw.get("target") or "").strip()
        if not value:
            return None
        typ = sanitize_label(
            str(raw.get("type") or raw.get("asset_type") or ""),
            default="other",
        )
        typ = refine_type(typ, value)
        notes = str(raw.get("notes") or "").strip()
        out = {"type": typ, "value": value[:1024]}
        if notes:
            out["notes"] = notes[:500]
        return out

    text = str(raw).strip()
    if not text:
        return None
    split = split_typed_line(text)
    if split is not None:
        typ, value = split
        return {"type": refine_type(typ, value), "value": value[:1024]}
    return {"type": infer_type(text), "value": text[:1024]}


def coerce_targets(raw: Iterable[Any] | None) -> list[dict[str, str]]:
    """Normalize targets; one entry per value (type is a soft hint)."""
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
        if _TYPE_RANK.get(target["type"], 0) > _TYPE_RANK.get(prev["type"], 0):
            by_value[key] = target
    return [by_value[k] for k in order]


def target_values(raw: Iterable[Any] | None) -> list[str]:
    return [t["value"] for t in coerce_targets(raw)]


def format_target(target: dict[str, str]) -> str:
    typ = target.get("type") or "other"
    value = target.get("value") or ""
    if typ == "other":
        return value
    return f"{typ}:{value}"


def format_targets(raw: Iterable[Any] | None) -> list[str]:
    return [format_target(t) for t in coerce_targets(raw)]


def infer_type(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return "other"
    if _URL_RE.match(s):
        return "url"
    if looks_like_ipv4(s):
        return "ip"
    if _EMAIL_RE.fullmatch(s):
        return "email"
    if _HASH_RE.fullmatch(s):
        return "hash"
    if _PATH_RE.search(s) and (s.startswith("/") or re.match(r"^[a-zA-Z]:\\", s)):
        return "path"
    if _HOST_RE.fullmatch(s):
        return "domain"
    digits = re.sub(r"[\s().\-]+", "", s)
    if s.startswith("+") or (digits.isdigit() and 7 <= len(digits) <= 15 and not _HOST_RE.search(s)):
        if _PHONE_RE.search(s) or (s.startswith("+") and digits[1:].isdigit()):
            return "phone"
    # Multi-word / capitalized → person heuristic
    if " " in s and not any(ch in s for ch in "./:@\\"):
        return "person"
    return "other"


def extract_targets(*texts: str) -> list[dict[str, str]]:
    """Pull candidate assets from free text (type hints are best-effort)."""
    blob = "\n".join(t for t in texts if t)
    found: list[dict[str, str]] = []

    def _add(typ: str, value: str) -> None:
        value = value.rstrip(".,;:")
        if not value or value.lower() in _STOP:
            return
        found.append({"type": refine_type(typ, value), "value": value[:1024]})

    for match in _URL_RE.finditer(blob):
        _add("url", match.group(0))
    for match in _EMAIL_RE.finditer(blob):
        _add("email", match.group(0))
    for match in _IP_RE.finditer(blob):
        _add("ip", match.group(0))
    for match in _IPV4_PARTIAL_RE.finditer(blob):
        raw = match.group(0).rstrip(".")
        if raw and not _IP_RE.fullmatch(raw):
            _add("ip", raw)
    for match in _HASH_RE.finditer(blob):
        _add("hash", match.group(0))
    for match in _PATH_RE.finditer(blob):
        _add("path", match.group(0))
    for match in _PHONE_RE.finditer(blob):
        raw = match.group(0).strip()
        if looks_like_ipv4(raw):
            continue
        digits = re.sub(r"\D", "", raw)
        if 7 <= len(digits) <= 15:
            _add("phone", raw)
    for match in _HOST_RE.finditer(blob):
        host = match.group(0)
        # Skip host part of emails already captured.
        if f"@{host}" in blob.lower() or f"@{host.lower()}" in blob.lower():
            # still allow apex domains that appear alone
            pass
        _add("domain", host)

    return coerce_targets(found)


def skill_allows_empty_scope(skill_name: str) -> bool:
    """Passive / planning skills may run with empty in_scope; probes may not."""
    name = (skill_name or "").strip()
    return not name or name not in ACTIVE_NETWORK_SKILLS


def roe_block_reason(skill_names: Iterable[str] | None, scope: Iterable[Any] | None) -> str | None:
    """Fail-closed when active probe skills have no in-scope *values*.

    Type labels are ignored — authorization is membership in in_scope.
    """
    names = [str(n).strip() for n in (skill_names or []) if str(n).strip()]
    if coerce_targets(scope):
        return None
    needing = [n for n in names if not skill_allows_empty_scope(n)]
    if not needing:
        return None
    return (
        "RoE fail-closed: in_scope has no authorized targets for "
        f"{', '.join(needing)} (add targets or deduce from brief)"
    )


# --- RoE provision (seed / in-scope / candidates) ---

def extract_scope_assets(*texts: str) -> list[dict[str, str]]:
    """Assets deduced from free text (type is a hint)."""
    return extract_targets(*texts)


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
    title+summary into in_scope. Type labels are soft hints only.
    """
    roe, _ = RulesOfEngagement.objects.get_or_create(project=project)
    deduced: list[str] = []
    scope = coerce_targets(roe.in_scope)
    if not scope:
        parts = list(texts or [])
        parts.extend([project.title or "", project.summary or ""])
        assets = extract_scope_assets(*parts)
        if assets:
            roe.in_scope = assets
            deduced = target_values(assets)
            if authorization and not roe.authorization_note:
                roe.authorization_note = authorization
            elif not roe.authorization_note:
                roe.authorization_note = (
                    "Auto-deduced from brief (operator did not supply RoE)."
                )
            # Seed keeps the human brief when we only deduced technical assets.
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
        # Normalize shape / collapse duplicate values (type is hint-only).
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
    return len(roe.candidates) - before
