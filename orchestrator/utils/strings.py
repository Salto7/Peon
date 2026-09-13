"""Value coerce + string / LLM-content helpers."""

from __future__ import annotations

import ast
import json
import re
from typing import Any

_TRUE = frozenset("1 true yes y on".split())
_FALSE = frozenset("0 false no n off".split())
_SEP = re.compile(r"[_\s]+")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_DASH_US = re.compile(r"[-_]+")
_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
_JSON_OBJ = re.compile(r"\{.*\}", re.S)
_THINK = frozenset("thinking reasoning thought".split())


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


def unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(i for i in items if i))


def fold_keys(token: str) -> list[str]:
    """Match keys: case / separators / punctuation stripped."""
    text = (token or "").strip().lower()
    return unique([text, _SEP.sub("-", text).strip("-"), _NON_ALNUM.sub("", text)]) if text else []


def name_forms(name: str) -> set[str]:
    """Mention variants: hyphen / underscore / space."""
    base = (name or "").strip().lower()
    if not base:
        return set()
    spaced = _DASH_US.sub(" ", base)
    return {base, spaced, spaced.replace(" ", "-"), spaced.replace(" ", "_")}


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
