"""Catalog CLI eligibility for a skill (tags + suggested/excluded)."""

from __future__ import annotations

from orchestrator.skills.model import Skill
from orchestrator.tools.catalog import CatalogTool, ToolCatalog
from orchestrator.utils.strings import as_str_list


def _norm_tags(values: list[str] | tuple[str, ...] | None) -> set[str]:
    return {str(t).strip().lower() for t in (values or []) if str(t).strip()}


def parse_id_list(raw: object) -> list[str]:
    """Comma/space-separated catalog ids from metadata strings."""
    if isinstance(raw, (list, tuple, set)):
        return [str(x).strip() for x in raw if str(x).strip()]
    return as_str_list(raw)


def eligible_catalog_tools(skill: Skill) -> list[CatalogTool]:
    """``(tool.tags ∩ skill.tags) ∪ suggested − excluded``."""
    catalog = ToolCatalog.shared().all()
    skill_tags = _norm_tags(skill.tags)
    suggested = {
        t.lower()
        for t in parse_id_list(getattr(skill, "suggested_tools", None) or [])
    }
    excluded = {
        t.lower()
        for t in parse_id_list(getattr(skill, "excluded_tools", None) or [])
    }
    out: list[CatalogTool] = []
    seen: set[str] = set()
    for tool in catalog.values():
        if tool.is_image_tier:
            continue
        tid = tool.id.lower()
        if tid in excluded or (tool.binary or "").lower() in excluded:
            continue
        tool_tags = _norm_tags(tool.tags)
        hit = bool(skill_tags & tool_tags) or tid in suggested or (
            tool.binary or ""
        ).lower() in suggested
        if not hit:
            continue
        if tid in seen:
            continue
        seen.add(tid)
        out.append(tool)
    return sorted(out, key=lambda t: t.id)


def eligible_cli_names(skill: Skill) -> list[str]:
    """Binary/id names to provision for a skill."""
    names: list[str] = []
    for tool in eligible_catalog_tools(skill):
        bin_name = (tool.binary or tool.id).strip()
        if bin_name:
            names.append(bin_name)
    # Legacy toolkit / requires_clis still honored.
    for extra in skill.toolkit or []:
        e = str(extra).strip()
        if e and e not in names:
            names.append(e)
    return names
