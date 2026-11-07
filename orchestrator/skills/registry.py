"""Skill catalog: one registry, one provider per style."""

from __future__ import annotations

import logging

from orchestrator.skills.providers.base import (
    CLAUDE_PROVIDER,
    PEON_PROVIDER,
    SkillDocument,
    SkillId,
)
from orchestrator.utils.paths import safe_join
from orchestrator.utils.service import SharedServiceBase

logger = logging.getLogger(__name__)

__all__ = ["SkillRegistry"]


def _document_from_record(rec) -> SkillDocument:
    """Build a runtime document from a platform catalog skill row."""
    from orchestrator.skills.providers.base import (
        DISCLOSURE_PROGRESSIVE,
        SkillSetup,
    )
    from orchestrator.skills.providers.tool_bridge import classify_claude_tools

    manifest = dict(rec.manifest or {})
    tools = tuple(str(item) for item in (manifest.get("tools") or ()))
    _, unmapped = classify_claude_tools(tools)
    setup_raw = rec.setup if isinstance(rec.setup, dict) else None
    setup = None
    if setup_raw and setup_raw.get("kind") in {"pip", "npm"}:
        setup = SkillSetup(
            kind=str(setup_raw.get("kind") or ""),
            cwd=str(setup_raw.get("cwd") or "."),
            manifest=str(setup_raw.get("manifest") or ""),
            post=tuple(
                str(c).strip()
                for c in (setup_raw.get("post") or ())
                if str(c).strip()
            ),
        )
    role_ids = tuple(
        b.role_id for b in (rec.bindings or ()) if float(b.score) >= 0.5
    )
    scores = tuple((b.role_id, float(b.score)) for b in (rec.bindings or ()) if b.role_id in role_ids)
    leads = tuple(b.role_id for b in (rec.bindings or ()) if b.lead and b.role_id in role_ids)
    parsed = SkillId.parse(rec.skill_id)
    if parsed is None:
        parsed = SkillId(provider=rec.provider or PEON_PROVIDER, name=rec.skill_id)
    name = parsed.name
    return SkillDocument(
        skill_id=parsed,
        description=rec.description,
        instructions=rec.body,
        root=None,
        tools=tools,
        allow_binaries=tuple(str(item) for item in (manifest.get("allow_binaries") or ())),
        tags=tuple(str(item) for item in (manifest.get("tags") or ())),
        disclosure=DISCLOSURE_PROGRESSIVE,
        role_ids=role_ids,
        lead_roles=leads,
        affinity_scores=scores,
        compatibility=str(manifest.get("compatibility") or ""),
        setup=setup,
        unmapped_tools=unmapped,
        container_root=f"/workspace/.peon/skills/{name}",
        container_setup_root=f"/workspace/.peon/skills/{name}",
        license=str(manifest.get("license") or ""),
        when_to_use=str(manifest.get("when_to_use") or ""),
        argument_hint=str(manifest.get("argument_hint") or ""),
        arguments=tuple(str(item) for item in (manifest.get("arguments") or ())),
        disallowed_tools=tuple(str(item) for item in (manifest.get("disallowed_tools") or ())),
        disable_model_invocation=bool(manifest.get("disable_model_invocation")),
        user_invocable=bool(manifest.get("user_invocable", True)),
        model=str(manifest.get("model") or ""),
        context=str(manifest.get("context") or ""),
        agent=str(manifest.get("agent") or ""),
        background=bool(manifest.get("background", True)),
        paths=tuple(str(item) for item in (manifest.get("paths") or ())),
        shell=str(manifest.get("shell") or ""),
        hooks=manifest.get("hooks") if isinstance(manifest.get("hooks"), dict) else None,
        raw_frontmatter=dict(manifest.get("raw_frontmatter") or {}),
    )


def _xml_text(value: str) -> str:
    return (
        (value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def available_skills_block(docs: list[SkillDocument]) -> str:
    """Progressive index, plus declared relations (with optional conditions) for listed skills.

    Sequencing/exclusivity and when a relation applies are catalog data — this is
    the one place they reach the model.
    """
    lines = ["<available_skills>"]
    for doc in docs:
        lines.append("<skill>")
        lines.append(f"<name>{_xml_text(doc.name)}</name>")
        lines.append(f"<description>{_xml_text(doc.description)}</description>")
        lines.append(f"<location>{_xml_text(doc.id)}</location>")
        lines.append("</skill>")
    lines.append("</available_skills>")
    relations = _relevant_skill_relations({doc.id for doc in docs})
    if relations:
        lines.append("<skill_relations>")
        lines.append(
            "<!-- sequence = skill runs after related. supersedes = skill replaces "
            "related for the same job. overlaps = similar purpose; prefer not to "
            "bind both for one job unless a human upgrades the row to "
            "preferred_over/supersedes. does_not_run_with = mutual exclusion. "
            "runs_under = sub-agent skill. depends_on = required prerequisite. "
            "preferred_over = keep skill, drop related. "
            "conditions empty = always; otherwise only when that condition fits. -->"
        )
        for rel in relations:
            note = _xml_text(rel.get("note") or "")
            cond = _xml_text(rel.get("conditions") or "")
            lines.append(
                f'<relation kind="{rel["kind"]}" skill="{_xml_text(rel["skill_id"])}" '
                f'related="{_xml_text(rel["related_skill_id"])}" conditions="{cond}">{note}</relation>'
            )
        lines.append("</skill_relations>")
    return "\n".join(lines)


def _relevant_skill_relations(ids: set[str]) -> list[dict[str, str]]:
    """Declared relations where both sides are in ``ids`` (the current prompt's skill set)."""
    from peon.projects.platform_catalog import PlatformCatalog

    try:
        rows = PlatformCatalog.shared().list_skill_relations()
    except Exception:
        return []
    return [r for r in rows if r["skill_id"] in ids and r["related_skill_id"] in ids]


class SkillRegistry(SharedServiceBase):
    """Scan the platform catalog and cache documents by ``SkillId.key``."""

    def __init__(self) -> None:
        self._by_key: dict[str, SkillDocument] | None = None
        self._stamp: str = ""

    def invalidate(self) -> None:
        self._by_key = None
        self._stamp = ""
        try:
            from orchestrator.utils.commands import clear_command_caches

            clear_command_caches()
        except Exception as exc:
            logger.warning("clearing command caches failed: %s", exc, exc_info=True)

    def _disk_mtime(self) -> float:
        """Legacy stamp helper. The catalog revision replaces directory mtimes."""
        try:
            return float(self._disk_stamp())
        except Exception:
            return 0.0

    def _disk_stamp(self) -> str:
        """Platform catalog revision. Disk trees are not the registry."""
        try:
            from peon.projects.platform_catalog import PlatformCatalog

            return str(PlatformCatalog.shared().revision())
        except Exception:
            return "0"

    def _scan(self) -> dict[str, SkillDocument]:
        out: dict[str, SkillDocument] = {}
        try:
            from peon.projects.platform_catalog import PlatformCatalog

            records = PlatformCatalog.shared().list_skills()
        except Exception:
            logger.debug("platform skill catalog unavailable", exc_info=True)
            return out
        for rec in records:
            doc = _document_from_record(rec)
            if doc.skill_id.key in out:
                logger.error("duplicate skill id %s; keeping first", doc.id)
                continue
            out[doc.skill_id.key] = doc
        return out

    def get_registry(self) -> dict[str, SkillDocument]:
        stamp = self._disk_stamp()
        if self._by_key is None or stamp != self._stamp:
            self._by_key = self._scan()
            self._stamp = stamp
        return self._by_key

    def get(self, skill_id: str) -> SkillDocument | None:
        parsed = SkillId.parse(skill_id)
        if parsed is None:
            return None
        if parsed.provider not in {PEON_PROVIDER, CLAUDE_PROVIDER}:
            return None
        return self.get_registry().get(parsed.key)

    def list_skills(self) -> list[SkillDocument]:
        return sorted(self.get_registry().values(), key=lambda s: (s.provider, s.id))

    def require(self, skill_id: str) -> SkillDocument:
        skill = self.get(skill_id)
        if skill is None:
            known = ", ".join(s.id for s in self.list_skills()) or "(none)"
            raise KeyError(f"unknown skill {skill_id!r}; known: {known}")
        return skill

    def load_text(self, skill_id: str, relative: str = "") -> str:
        skill = self.require(skill_id)
        if not relative:
            return skill.prompt_block()
        try:
            from peon.projects.platform_catalog import PlatformCatalog

            rec = PlatformCatalog.shared().get_skill(skill.id)
        except Exception:
            rec = None
        if rec is not None and relative in rec.files:
            return rec.files[relative]
        if skill.root is None:
            raise FileNotFoundError(f"skill {skill_id!r} missing {relative}")
        path = safe_join(skill.root, relative)
        if path is None or not path.is_file():
            raise FileNotFoundError(f"skill {skill_id!r} missing {relative}")
        return path.read_text(encoding="utf-8")

    def usable(self, skill_id: str, *, role_id: str = "") -> SkillDocument | None:
        """Return a skill when its dynamically inferred roles permit binding."""
        doc = self.get(skill_id)
        if doc is None:
            return None
        role = (role_id or "").strip().lower()
        if doc.provider == PEON_PROVIDER and not role:
            return doc
        if not role or role not in doc.role_ids:
            return None
        return doc

    def skills_for_role(self, role_id: str) -> tuple[str, ...]:
        """Skill public ids whose inferred affinity includes ``role_id``.

        Ordered: lead skills first, then higher affinity score, then name.
        Hard catalog relations (preferred_over, does_not_run_with, …) reshape
        the list so Peon respects them at bind time.
        """
        from orchestrator.skills.relations import apply_skill_relations

        rid = (role_id or "").strip().lower()
        if not rid:
            return ()
        ranked: list[tuple[float, float, str]] = []
        for doc in self.list_skills():
            if rid not in (doc.role_ids or ()):
                continue
            score_map = dict(doc.affinity_scores or ())
            score = float(score_map.get(rid, 1.0))
            lead = 0.0 if rid in (doc.lead_roles or ()) else 1.0
            ranked.append((lead, -score, doc.id))
        ranked.sort()
        return apply_skill_relations([item[2] for item in ranked])

    def lead_skill_for_role(self, role_id: str) -> str:
        """Primary lead skill for a role (first after affinity ranking)."""
        bound = self.skills_for_role(role_id)
        return bound[0] if bound else ""
