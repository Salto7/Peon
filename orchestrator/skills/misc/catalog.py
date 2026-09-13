"""Shared skill-catalog formatting (registry / load / router)."""

from __future__ import annotations

from typing import Iterable, Literal

from orchestrator.skills.misc.skill import Skill

CatalogMode = Literal["index", "llm", "discover"]


def filter_skills(
    skills: Iterable[Skill],
    *,
    jobable_only: bool = False,
    tags: list[str] | None = None,
) -> list[Skill]:
    out = list(skills)
    if jobable_only:
        out = [s for s in out if s.jobable]
    if tags:
        tag_set = {str(t).strip() for t in tags if str(t).strip()}
        out = [s for s in out if tag_set.intersection(s.tags or [])]
    return out


def _format_skill_line(skill: Skill, *, mode: CatalogMode) -> str:
    if mode == "discover":
        return f"- {skill.name}: {skill.description}"

    if mode == "llm":
        flags: list[str] = []
        if skill.category:
            flags.append(f"category={skill.category}")
        if skill.tags:
            flags.append("tags=" + ",".join(skill.tags))
        if skill.direct_answer:
            flags.append("direct_answer=true")
        if skill.lifecycle:
            flags.append(f"lifecycle={skill.lifecycle}")
        if skill.toolkit:
            flags.append("toolkit=" + ",".join(skill.toolkit[:8]))
        if skill.tools:
            flags.append("tools=" + ",".join(skill.tools[:8]))
        if skill.is_mcp:
            flags.append("mcp=true")
        meta = f" ({'; '.join(flags)})" if flags else ""
        desc = (skill.description or "").strip().replace("\n", " ")
        if len(desc) > 220:
            desc = desc[:217] + "…"
        return f"- {skill.name}{meta}: {desc}"

    # index
    hint = f" [lifecycle={skill.lifecycle}]" if skill.lifecycle else ""
    cat = f" category={skill.category}" if skill.category else ""
    tag_s = f" tags={','.join(skill.tags)}" if skill.tags else ""
    assets_hint = f" ({len(skill.assets)} assets)" if skill.assets else ""
    return f"- {skill.name}:{cat}{tag_s}{hint}{assets_hint} — {skill.description}"


def _collect_categories(skills: Iterable[Skill]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for s in skills:
        cat = (s.category or "").strip()
        if cat and cat not in seen:
            seen.add(cat)
            out.append(cat)
    return sorted(out)


def format_catalog(
    skills: Iterable[Skill],
    *,
    mode: CatalogMode = "index",
    header: str | None = None,
    empty: str = "No skills installed.",
) -> str:
    items = list(skills)
    if not items:
        return empty

    sort_key = (
        (lambda s: (s.category or "", s.name)) if mode == "index" else (lambda s: s.name)
    )
    lines: list[str] = []
    if header:
        lines.append(header)
        if mode == "index":
            cats = _collect_categories(items)
            cat_line = (
                "Categories in this catalog: " + " | ".join(cats)
                if cats
                else "Categories: (none declared on skills)."
            )
            lines.append(
                cat_line.rstrip(".")
                + ". Tags = domains/techniques declared on each skill."
            )
    lines.extend(_format_skill_line(s, mode=mode) for s in sorted(items, key=sort_key))
    return "\n".join(lines)
