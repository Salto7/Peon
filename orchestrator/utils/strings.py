"""Value coerce + string / LLM-content helpers."""

from __future__ import annotations

import ast
import json
import re
from typing import Any

_TRUE = frozenset(["1", "true", "yes", "y", "on"])
_FALSE = frozenset(["0", "false", "no", "n", "off"])
_KEBAB = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
_JSON_OBJ = re.compile(r"\{.*\}", re.S)
_THINK = frozenset(["thinking", "reasoning", "thought"])


def is_env_name(name: str) -> bool:
    """True for a POSIX-ish env var name (``FOO_BAR``, ``_x``)."""
    return bool(ENV_NAME_RE.match((name or "").strip()))


def normalize_env_map(raw: Any) -> dict[str, str]:
    """Keep non-empty ``str(k)→str(v)`` pairs; drop None/blank values."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        name = str(key or "").strip()
        if name:
            out[name] = text
    return out


def expand_id_tokens(raw: str, *, min_len: int = 2) -> set[str]:
    """Lower/strip an id and split on ``/ _ . -`` into affinity tokens."""
    tokens: set[str] = set()
    t = str(raw or "").strip().lower()
    if len(t) < min_len:
        return tokens
    tokens.add(t)
    for part in t.replace("/", " ").replace("_", "-").replace(".", " ").split("-"):
        p = part.strip()
        if len(p) >= min_len:
            tokens.add(p)
            tokens.update(w for w in p.split() if len(w) >= min_len)
    return tokens


def collapse_ws(text: str | None) -> str:
    """Collapse runs of whitespace to a single space (trim ends)."""
    return " ".join((text or "").split())


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str, *, max_len: int = 64) -> str:
    """Lowercase kebab slug for memory/gist names."""
    return _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")[:max_len]


def is_kebab(value: str) -> bool:
    """True for a lowercase kebab-case id (``nmap``, ``http-prober``)."""
    return bool(_KEBAB.match((value or "").strip()))


def as_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    return True if text in _TRUE else False if text in _FALSE else default


def as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [p for p in (x.strip() for x in value.replace(";", ",").split(",")) if p]
    if isinstance(value, (list, tuple, set)):
        return [s for s in (str(p).strip() for p in value) if s]
    text = str(value).strip()
    return [text] if text else []


def unique(values: Any) -> list[str]:
    """Order-preserving dedupe of stringifiable items (empty dropped)."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in values or ():
        item = str(raw).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def plain_text(content: Any) -> str:
    """LLM message content → plain text (drops thinking blocks)."""
    if content is None:
        return ""
    if isinstance(content, str):
        s = content.strip()
        if s and s[0] in "[{" and ("'type'" in s or '"type"' in s):
            for parse in (_literal, _json):
                if (parsed := parse(s)) is not None:
                    return plain_text(parsed)
        return s
    if isinstance(content, list):
        return "\n\n".join(p for b in content if (p := plain_text(b))).strip()
    if isinstance(content, dict):
        if str(content.get("type") or "").lower() in _THINK:
            return ""
        for key in ("text", "content"):
            if content.get(key) is not None:
                return plain_text(content[key])
        return ""
    return str(content).strip()


def extract_json(raw: str) -> str:
    """Best-effort JSON object string from fenced or bare LLM output."""
    text = (raw or "").strip()
    if "```" in text and (m := _JSON_FENCE.search(text)):
        return m.group(1)
    m = _JSON_OBJ.search(text)
    return m.group(0) if m else text


def _literal(s: str):
    try:
        return ast.literal_eval(s)
    except Exception:
        return None


def _json(s: str):
    try:
        return json.loads(s)
    except Exception:
        return None
