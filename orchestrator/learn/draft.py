"""Tool / catalog helpers for Learn authoring (roles + tool recipes)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from orchestrator.prompts import CATALOG_INSTALL_PREFER
from orchestrator.tools.catalog import ToolCatalog

_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9._+-]{1,31}")
CATALOG_INSTALL_TYPES = ("apt", "github_release", "git_clone", "pip", "custom")

__all__ = [
    "CATALOG_INSTALL_TYPES",
    "CatalogResolution",
    "authoring_prompt",
    "catalog_summaries",
    "filter_catalog",
    "install_recipe_from_suggestion",
    "prompt_catalog_hits",
    "propose_missing_tool",
    "proposed_tool_match",
    "resolve_catalog_keys",
    "tool_replan_human",
    "tool_suggest_human",
    "tool_suggestion_from_payload",
]


@dataclass
class CatalogResolution:
    matched: list[dict[str, str]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def mode(self) -> str:
        return "catalog_wrapper" if self.matched else "capability"

    @property
    def primary_binary(self) -> str:
        return self.matched[0]["binary"] if self.matched else ""


def catalog_summaries(*, limit: int = 80) -> list[dict[str, str]]:
    return ToolCatalog.shared().summaries(limit=limit)


def resolve_catalog_keys(keys: list[str]) -> CatalogResolution:
    matched: list[dict[str, str]] = []
    missing: list[str] = []
    seen_keys: set[str] = set()
    seen_ids: set[str] = set()
    catalog = ToolCatalog.shared()
    for raw in keys:
        key = (raw or "").strip().lower()
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        tool = catalog.lookup(key)
        if tool is None:
            missing.append(key)
            continue
        if tool.id in seen_ids:
            continue
        seen_ids.add(tool.id)
        matched.append(
            {
                "id": tool.id,
                "binary": (tool.binary or tool.id).strip(),
                "description": (tool.description or "")[:240],
            }
        )
    return CatalogResolution(matched=matched, missing=missing)


def prompt_catalog_hits(prompt: str) -> list[dict[str, str]]:
    """Catalog tools named in free text (match-only; ignores unknown tokens)."""
    return resolve_catalog_keys(
        [t.lower() for t in _TOKEN_RE.findall(prompt or "")]
    ).matched


def proposed_tool_match(suggestion: dict[str, Any]) -> dict[str, str]:
    """Normalize a tools-suggestor result into a catalog hit shape."""
    parsed = suggestion.get("parsed") if isinstance(suggestion.get("parsed"), dict) else {}
    tid = str(suggestion.get("id") or parsed.get("id") or "").strip()
    binary = str(parsed.get("binary") or tid).strip() or tid
    desc = str(parsed.get("description") or suggestion.get("notes") or "").strip()
    return {"id": tid, "binary": binary, "description": desc[:240]}


def tool_suggestion_from_payload(
    payload: dict[str, Any], *, author: str = ""
) -> dict[str, Any]:
    """Normalize tools-suggestor / OpenCode JSON into a recipe dict."""
    if not isinstance(payload, dict):
        raise RuntimeError("tools-suggestor returned non-object JSON")
    yaml_text = str(payload.get("yaml") or "").strip()
    if not yaml_text:
        raise RuntimeError("tools-suggestor returned empty yaml")
    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise RuntimeError(f"suggested YAML is invalid: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("suggested YAML must be a mapping")
    tid = str(payload.get("id") or parsed.get("id") or "").strip()
    if not tid:
        raise RuntimeError("suggested tool missing id")
    out: dict[str, Any] = {
        "id": tid,
        "yaml": yaml_text,
        "install_script": str(payload.get("install_script") or "").strip(),
        "notes": str(payload.get("notes") or "").strip(),
        "parsed": parsed,
    }
    if author:
        out["author"] = author
    return out


def install_recipe_from_suggestion(suggestion: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": suggestion["id"],
        "yaml": suggestion["yaml"],
        "install_script": suggestion.get("install_script") or "",
        "notes": suggestion.get("notes") or "",
    }


def tool_suggest_human(prompt: str) -> str:
    return (
        f"Operator request:\n{prompt}\n\n"
        f"Existing catalog tools (avoid duplicate ids):\n"
        f"{yaml.safe_dump(catalog_summaries(), sort_keys=False)}"
    )


def tool_replan_human(
    *,
    prompt: str,
    yaml_text: str,
    install_script: str = "",
    error: str = "",
    feedback: str = "",
) -> str:
    text = (prompt or "").strip() or "Revise the catalog install recipe."
    fix = (feedback or "").strip()
    parts = [
        f"Operator request:\n{text}",
        f"Previous YAML (failed install test):\n```yaml\n{yaml_text}\n```",
        f"Previous install script:\n```bash\n{install_script or '(none)'}\n```",
        f"Install/test error:\n{error or '(unknown)'}",
    ]
    if fix:
        parts.append(f"Operator fix guidance:\n{fix}")
    parts.append(
        "Produce a revised catalog YAML that is more likely to install on "
        "debian:bookworm-slim. Keep the same tool intent as the operator "
        "request; fix install/verify based on the error"
        + (" and fix guidance." if fix else ".")
        + f" {CATALOG_INSTALL_PREFER}\n"
        f"Existing catalog tools:\n{yaml.safe_dump(catalog_summaries(), sort_keys=False)}"
    )
    return "\n\n".join(parts)


def filter_catalog(
    catalog: list[dict[str, str]], tools: list[str] | None
) -> list[dict[str, str]]:
    if not tools:
        return catalog
    wanted = {t.strip().lower() for t in tools if str(t).strip()}
    return [t for t in catalog if t["id"] in wanted] or catalog


def propose_missing_tool(
    prompt: str, suggest_fn
) -> tuple[dict[str, Any] | None, list[dict[str, str]], list[dict[str, Any]]]:
    """If prompt names no catalog CLI, call ``suggest_fn`` and shape recipes."""
    if prompt_catalog_hits(prompt):
        return None, [], []
    suggestion = suggest_fn(prompt)
    return (
        suggestion,
        [proposed_tool_match(suggestion)],
        [install_recipe_from_suggestion(suggestion)],
    )


def authoring_prompt(
    prompt: str,
    catalog: list[dict[str, str]],
    *,
    proposed: list[dict[str, str]] | None = None,
    tool_recipes: list[dict[str, Any]] | None = None,
) -> str:
    """Human message for role-pack authoring (ROLE.yaml under roles/)."""
    on_disk = prompt_catalog_hits(prompt)
    proposed = list(proposed or [])
    hits = list(on_disk)
    seen = {h["id"] for h in hits}
    for item in proposed:
        if item["id"] not in seen:
            hits.append(item)
            seen.add(item["id"])
    install_types = ", ".join(CATALOG_INSTALL_TYPES)
    lines = [
        f"Operator request:\n{prompt}\n",
        f"Catalog tools matched on disk:\n"
        f"{yaml.safe_dump(on_disk or ['(none)'], sort_keys=False)}",
        "Author a CrewAI role pack: ROLE.yaml (+ KNOWLEDGE.md, optional references/).",
        "Set id (kebab-case), goal, crew_role/label, tools list, and any flags needed.",
    ]
    if tool_recipes:
        lines.append(
            "CLI is NOT in tools/catalog yet. Host drafted install recipe(s) "
            f"(types: {install_types}). Prefer referencing recipe id(s) in "
            f"ROLE.yaml tools when appropriate.\n"
            f"{yaml.safe_dump(tool_recipes, sort_keys=False)}"
        )
    elif hits:
        lines.append(
            "Matched catalog tools — prefer those ids in ROLE.yaml tools."
        )
    lines.append(f"Available catalog tools:\n{yaml.safe_dump(catalog, sort_keys=False)}")
    return "\n".join(lines)
