"""Argv / stdin / corp-osint handoff helpers."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


def read_tokens(
    argv: list[str],
    *,
    skip_hash: bool = True,
    read_stdin: bool = True,
) -> list[str]:
    tokens = [a.strip() for a in argv if a.strip()]
    if read_stdin and not tokens and not sys.stdin.isatty():
        tokens = []
        for ln in sys.stdin:
            s = ln.strip()
            if not s:
                continue
            if skip_hash and s.startswith("#"):
                continue
            tokens.append(s)
    return tokens


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        value = (item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _rows_from_payload(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        findings = data.get("findings")
        if isinstance(findings, list):
            return [row for row in findings if isinstance(row, dict)]
    return []


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_noncomment_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


_LEGAL_SUFFIX = re.compile(
    r"\b(llc|inc|corp|corporation|ltd|limited|gmbh|s\.a\.|sa|pte|pty|holdings?|company|co)\b",
    re.I,
)


def _entity_brand_key(name: str) -> str:
    text = _LEGAL_SUFFIX.sub(" ", (name or "").lower())
    return re.sub(r"[^a-z0-9]+", "", text)


def _entity_tokens(name: str) -> list[str]:
    text = _LEGAL_SUFFIX.sub(" ", (name or "").lower())
    return [t for t in re.split(r"[^a-z0-9]+", text) if len(t) >= 4]


def _domain_label(domain: str) -> str:
    host = (domain or "").strip().lower().split(".")[0]
    return re.sub(r"[^a-z0-9]+", "", host)


def _brand_match(entity: str, domain: str) -> bool:
    tokens = _entity_tokens(entity)
    dl = _domain_label(domain)
    if len(dl) < 3 or len(tokens) != 1:
        return False
    token = tokens[0]
    return dl == token or dl in token or token in dl


def _provenance_map(root: Path) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for rel in (
        root / "raw" / "ai-osint-subsidiaries" / "entities.full.json",
        root / "raw" / "corp-osint" / "entities.full.json",
    ):
        full = rel
        if not full.is_file():
            continue
        data = _read_json(full)
        if data is None:
            continue
        rows = _rows_from_payload(data)
        for row in rows:
            key = _entity_brand_key(str(row.get("entity") or ""))
            if not key:
                continue
            out.setdefault(key, []).append(row)
        if out:
            return out
    return out


def load_corp_handoff_details(workspace: Path | str = "workspace") -> dict[str, Any]:
    """Load corp handoff and filter low-confidence AI domains from seed domains.

    Returns dict with names, domains, and excluded_low_confidence domains.
    """
    root = Path(workspace)
    names: list[str] = []
    domains: list[str] = []
    excluded: list[dict[str, str]] = []
    prov = _provenance_map(root)
    entities = root / "corp-entities.json"
    if entities.is_file():
        data = _read_json(entities) or []
        for row in _rows_from_payload(data):
            entity = str(row.get("entity") or "").strip()
            if entity:
                names.append(entity)
            domain = str(row.get("domain") or "").strip().lower()
            if not domain:
                continue
            key = _entity_brand_key(entity)
            source_type = "unknown"
            confidence = "unknown"
            if key in prov:
                matches = [
                    r for r in prov[key] if str(r.get("domain") or "").strip().lower() == domain
                ]
                pick = matches[0] if matches else prov[key][0]
                source_type = str(pick.get("source") or "").strip().lower() or "unknown"
                confidence = str(pick.get("confidence") or "").strip().lower() or "unknown"
            if source_type == "ai" and confidence == "low" and not _brand_match(entity, domain):
                excluded.append(
                    {
                        "entity": entity,
                        "domain": domain,
                        "reason": "low-confidence ai domain (not brand-matched)",
                    }
                )
                continue
            domains.append(domain)
    names.extend(_read_noncomment_lines(root / "corp-seeds.txt"))
    domains.extend(line.lower() for line in _read_noncomment_lines(root / "corp-domains.txt"))
    return {
        "names": _unique(names),
        "domains": _unique(domains),
        "excluded_low_confidence": excluded,
    }


def load_corp_handoff(workspace: Path | str = "workspace") -> tuple[list[str], list[str]]:
    """Load entity names and apex domains from corp-osint compact JSON or seed files.

    Accepts compact ``[{entity, relationship, domain, source}]`` and the older
    ``{findings: [...]}`` envelope. Falls back to ``corp-seeds.txt`` /
    ``corp-domains.txt``. Does not parse markdown.
    """
    details = load_corp_handoff_details(workspace)
    return list(details["names"]), list(details["domains"])
