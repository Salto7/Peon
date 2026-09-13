"""Registrable (apex / eTLD+1) domain extraction from mixed tool output."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

_URL_RE = re.compile(r"https?://[^\s\"'<>\\]+", re.I)
HOST_RE = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\b",
    re.I,
)
_JSON_DOMAIN_KEYS = frozenset(
    {
        "domain",
        "apexdomain",
        "apex_domain",
        "registrable_domain",
        "root_domain",
        "hostname",
        "host",
        "name",
        "ldhname",
        "url",
        "uri",
        "link",
        "page_url",
        "http_url",
    }
)

try:
    import tldextract  # type: ignore

    # Offline: bundled PSL snapshot only (sandbox-friendly, no HTTP fetch).
    _EXTRACT = tldextract.TLDExtract(
        suffix_list_urls=(),
        fallback_to_snapshot=True,
        include_psl_private_domains=False,
    )
except Exception:
    _EXTRACT = None

HAS_TLDEXTRACT = _EXTRACT is not None


def public_suffixes() -> list[str]:
    """ICANN public suffixes from tldextract's PSL (includes co.uk, com.au, …).

    Skips PSL wildcards (`*.ck`), exceptions (`!city.kawasaki.jp`), and private
    domains (blogspot.com, …). Requires tldextract.
    """
    if _EXTRACT is None:
        raise RuntimeError(
            "tldextract is required for related-tlds / apex parsing — "
            "pip install tldextract"
        )
    raw = list(_EXTRACT.tlds)
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        s = (item or "").strip().lower().lstrip(".")
        if not s or s in seen:
            continue
        if s.startswith("!") or "*" in s:
            continue
        seen.add(s)
        out.append(s)
    return sorted(out)


def _strip_noise(token: str) -> str:
    t = (token or "").strip().rstrip(".,;:)]}>\"'")
    return t.lstrip("([{\"'")


def host_from_token(token: str) -> str:
    t = _strip_noise(token).lower()
    if not t or t in {"localhost", "example.com", "example.org"}:
        return ""
    if "://" not in t and "/" not in t and "@" not in t:
        host = t.split(":")[0]
    else:
        if "://" not in t:
            t = "http://" + t
        try:
            host = (urlparse(t).hostname or "").lower()
        except Exception:
            return ""
    host = host.strip(".").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host or not HOST_RE.fullmatch(host):
        return ""
    return host


def to_apex(host: str, *, keep_hosts: bool = False) -> str:
    host = host_from_token(host)
    if not host:
        return ""
    if keep_hosts:
        return host
    if _EXTRACT is None:
        print(
            "# tldextract required for apex parsing (co.uk etc.) — pip install tldextract",
            file=sys.stderr,
        )
        return ""
    ext = _EXTRACT(host)
    if not ext.suffix or not ext.domain:
        return ""
    return f"{ext.domain}.{ext.suffix}".lower()


def sld_label(seed: str) -> str:
    """Registrable label (example.com → example; foo.co.uk → foo)."""
    host = host_from_token(seed) or (seed or "").strip().lower()
    if not host:
        return ""
    if _EXTRACT is None:
        print(
            "# tldextract required for sld_label — pip install tldextract",
            file=sys.stderr,
        )
        return ""
    ext = _EXTRACT(host)
    return (ext.domain or "").lower()


def _walk_json(obj: Any, out: list[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k).lower().replace("-", "_")
            if key in _JSON_DOMAIN_KEYS and isinstance(v, str):
                out.append(v)
            else:
                _walk_json(v, out)
    elif isinstance(obj, list):
        for item in obj:
            _walk_json(item, out)
    elif isinstance(obj, str):
        if "://" in obj or ("." in obj and " " not in obj.strip()):
            out.append(obj)


def extract_from_text(text: str) -> list[str]:
    found: list[str] = []
    if not text:
        return found
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            _walk_json(json.loads(stripped), found)
        except json.JSONDecodeError:
            pass
    found.extend(_URL_RE.findall(text))
    found.extend(HOST_RE.findall(text))
    return found


def extract_from_path(path: Path) -> list[str]:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"# skip {path}: {exc}", file=sys.stderr)
        return []
    try:
        tokens: list[str] = []
        _walk_json(json.loads(raw), tokens)
        if tokens:
            return tokens
    except json.JSONDecodeError:
        pass
    return extract_from_text(raw)


def collect(
    texts: Iterable[str],
    files: Iterable[Path],
    *,
    keep_hosts: bool = False,
    exclude_suffixes: set[str] | None = None,
) -> list[str]:
    raw: list[str] = []
    for t in texts:
        raw.extend(extract_from_text(t))
    for p in files:
        raw.extend(extract_from_path(p))
    seen: set[str] = set()
    out: list[str] = []
    excl = {e.lower().lstrip(".") for e in (exclude_suffixes or set()) if e}
    for tok in raw:
        d = to_apex(tok, keep_hosts=keep_hosts)
        if not d or d in seen:
            continue
        if any(d == e or d.endswith("." + e) for e in excl):
            continue
        seen.add(d)
        out.append(d)
    return sorted(out)
