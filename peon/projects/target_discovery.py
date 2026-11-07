"""Asset discovery from findings and LLM suggestions.

Producer types are an open vocabulary: any slug on ``subjects`` / RoE /
``accept_asset`` is stored and shown. Subjects come from typed ``subjects``
(or metadata aliases) plus structured ``host``/``port`` — nothing else.
"""

from __future__ import annotations

from typing import Any

from orchestrator.crew.roles.registry import RoleRegistry
from orchestrator.utils.llm import chat_json, llm_configured
from peon.projects.asset_model import RELATIONS, classify, is_bare_port_value
from peon.projects.targets import (
    accept_asset,
    canonical_asset_key,
    coerce_target,
    coerce_targets,
    format_service,
    merge_typed_rows,
    resolve_service,
    sanitize_label,
)


def active_probe_role_ids() -> frozenset[str]:
    """Technical roles that probe targets (RoE owned by the engagement)."""
    try:
        return frozenset(
            r.id for r in RoleRegistry.shared().list_roles() if r.is_technical
        )
    except Exception:
        return frozenset()


_LLM_ASSET_SYSTEM = """You extract engagement subjects for Peon (authorized analysis).
Return JSON only: {"assets":[{"type":"<slug>","value":"<string>"}]}
\"type\" is any free-form lowercase slug that fits the subject (not a fixed
vocabulary) — e.g. person, path, function, vuln, ip, url, file, hash, … Prefer
type \"asset\" for a bare organization or brand name. Only include subjects
clearly named. No speculation. If none, return {"assets":[]}.
"""


def _item_type_value(item: Any) -> tuple[str, str]:
    """``(type hint, value)`` for a raw subjects-list entry, str or dict."""
    if isinstance(item, str):
        return "", item.strip()
    if isinstance(item, dict):
        return str(item.get("type") or ""), str(item.get("value") or "").strip()
    return "", ""


def _sibling_host(raw: list) -> str:
    """First value in ``raw`` that is a valid ``port`` relation source.

    ``asset_model.RELATIONS["port"]`` declares which classes a port lives on
    and what it targets — reused here rather than re-declared.
    """
    sources = RELATIONS["port"].sources
    for item in raw:
        hint, value = _item_type_value(item)
        if value and classify(hint, value).slug in sources:
            return value
    return ""


def _normalize_subjects(raw: Any) -> list[dict[str, str]]:
    """Coerce an explicit subjects list into typed {type,value[,relation]} rows."""
    if not isinstance(raw, list):
        return []
    host = _sibling_host(raw)
    out: list[dict[str, str]] = []

    for item in raw:
        if isinstance(item, str):
            coerced = accept_asset(item.strip())
        elif isinstance(item, dict):
            hint, value = _item_type_value(item)
            if hint == "service" or is_bare_port_value(hint, value):
                service = resolve_service(value, host=host)
                if not service:
                    continue
                item = {**item, "type": "service", "value": service}
            coerced = coerce_target(item)
        else:
            continue
        if not coerced or not coerced.get("value"):
            continue
        rel_raw = (
            str(item.get("relation") or "")
            if isinstance(item, dict)
            else ""
        )
        out.append(
            {
                "type": sanitize_label(str(coerced.get("type") or ""), default="other")
                or "other",
                "value": str(coerced["value"]).strip()[:1024],
                "relation": sanitize_label(rel_raw, default="affected", max_len=32)
                or "affected",
            }
        )

    def _merge_relation(prev: dict[str, str], row: dict[str, str]) -> None:
        if prev.get("relation") == "parent" and row.get("relation") != "parent":
            prev["relation"] = row["relation"]

    return merge_typed_rows(out, on_dup=_merge_relation)


def subjects_from_finding(row: dict[str, Any] | None) -> list[dict[str, str]]:
    """Subjects = typed ``subjects`` (+ metadata aliases) and/or ``host``/``port``.

    Provenance (``evidence``, ``evidence_path``) is never a subject. Open type
    vocabulary — producer slugs are kept as-is.
    """
    if not isinstance(row, dict):
        return []

    host = str(row.get("host") or "").strip()
    asset_type = sanitize_label(str(row.get("asset_type") or ""), default="")
    port = row.get("port")
    protocol = sanitize_label(str(row.get("protocol") or ""), default="", max_len=16)

    host_port_rows: list[dict[str, str]] = []
    if host:
        pair: list[dict[str, str]] = [{"type": asset_type or "host", "value": host}]
        try:
            port_i = int(port) if port is not None and str(port).strip() != "" else None
        except (TypeError, ValueError):
            port_i = None
        if port_i is not None and 1 <= port_i <= 65535:
            service = format_service(host, port_i, protocol)
            if service:
                pair.append({"type": "service", "value": service})
        host_port_rows = _normalize_subjects(pair)

    explicit = row.get("subjects")
    if not explicit and isinstance(row.get("metadata"), dict):
        meta = row["metadata"]
        explicit = meta.get("subjects") or meta.get("assets")
    if explicit:
        merged = _normalize_subjects(explicit)
        known = {canonical_asset_key(r["type"], r["value"]) for r in merged}
        merged.extend(
            r
            for r in host_port_rows
            if canonical_asset_key(r["type"], r["value"]) not in known
        )
        return merged

    return host_port_rows


_SCOPE_CLASSIFIER_SYSTEM = """You help a human operator triage newly discovered assets during an authorized security engagement.
Given the engagement's authorization context and a list of newly discovered candidate targets (not yet authorized to probe), label each one:
- "target": clearly part of the engagement's own organization/infrastructure
- "third_party": clearly unrelated third-party infrastructure (CDN, cloud host, SaaS, analytics, unrelated vendor)
- "uncertain": not clearly either from the context given
Return JSON only: {"classifications": [{"value": "<exact target value>", "label": "target"|"third_party"|"uncertain", "reason": "<one short clause>"}]}
Advisory only — this never authorizes or blocks probing, it only helps a human reviewing the candidate list. Be conservative: prefer "uncertain" over a confident wrong guess."""


def classify_scope_candidates(
    context: str, values: list[str]
) -> dict[str, tuple[str, str]]:
    """Soft (advisory-only) target-vs-third-party label per candidate value.

    Core 1.5: advisory only on RoE candidates — never authorizes or blocks.
    Failures yield no labels, never an exception.
    """
    wanted = [v.strip() for v in values if v and v.strip()]
    if not wanted:
        return {}
    try:
        if not llm_configured():
            return {}
        from orchestrator.utils import llm_usage

        human = (
            f"Authorization context:\n{(context or '(none given)').strip()[:2000]}\n\n"
            "Newly discovered candidates:\n" + "\n".join(f"- {v}" for v in wanted)
        )
        with llm_usage.purpose("scope_candidate_classifier"):
            data = chat_json(_SCOPE_CLASSIFIER_SYSTEM, human)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    raw = data.get("classifications")
    if not isinstance(raw, list):
        return {}
    valid = {"target", "third_party", "uncertain"}
    out: dict[str, tuple[str, str]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value") or "").strip()
        label = str(item.get("label") or "").strip().lower()
        if not value or label not in valid:
            continue
        reason = str(item.get("reason") or "").strip()[:160]
        out[value.lower()] = (label, reason)
    return out


def suggest_assets(text: str) -> list[dict[str, str]]:
    """LLM as a discovery producer — returns typed assets; peon trusts the types."""
    try:
        if not llm_configured():
            return []
        from orchestrator.utils import llm_usage

        with llm_usage.purpose("target_discovery"):
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

