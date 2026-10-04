"""Asset discovery from findings and LLM suggestions."""

from __future__ import annotations

from typing import Any

from orchestrator.crew.roles.registry import RoleRegistry
from orchestrator.utils.llm import chat_json, llm_configured
from peon.projects.target_shapes import (
    accept_asset,
    coerce_target,
    coerce_targets,
    detect_shape,
    sanitize_label,
    url_host,
)


def _active_probe_role_ids() -> frozenset[str]:
    """Roles that require RoE before networked work (from ROLE.yaml)."""
    try:
        return frozenset(
            r.id for r in RoleRegistry.shared().list_roles() if r.requires_roe
        )
    except Exception:
        return frozenset()

_LLM_ASSET_SYSTEM = """You extract engagement subjects for Peon (authorized analysis).
Return JSON only: {"assets":[{"type":"<slug>","value":"<string>"}]}
type is a free-form lowercase slug from the operator's wording (examples: ip, fqdn,
url, netblock, file, malware, sample, source, repo, package, hash, email,
organization, person). Only include subjects clearly named. No speculation.
If none, return {"assets":[]}."""

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

def _llm_suggest_assets(text: str) -> list[dict[str, str]]:
    """LLM as a discovery producer — returns typed assets; peon trusts the types."""
    try:
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
