"""Pick default skills for a job description (catalog-driven categories/tags)."""

from __future__ import annotations

import json
import logging
import re
from typing import Iterable

from orchestrator.skills.misc.catalog import filter_skills, format_catalog
from orchestrator.skills.misc.mcp import looks_like_mcp_request
from orchestrator.skills.misc.skill import Skill
from orchestrator.skills.misc.matcher import SkillNameMatcher
from orchestrator.skills.misc.registry import SkillRegistry
from orchestrator.utils.llm import chat_text
from orchestrator.utils.service import SharedService
from orchestrator.utils.strings import extract_json, plain_text

logger = logging.getLogger(__name__)

DELIVERABLE_RE = re.compile(
    r"\b("
    r"create|generate|produce|build|make|write|export|draw|render|"
    r"diagram|flowchart|chart|plot|org(?:anization)?\s*chart|"
    r"pdf|html|svg|png|jpg|jpeg|report|artifact|document|slide"
    r")\b",
    re.I,
)


class SkillRouter(SharedService):
    """Resolve default skills: explicit → name match → heuristics → LLM."""

    SYSTEM_PROMPT = """You are the skill router (progressive disclosure).

Given a user job and a compact catalog of selectable skills, pick the skill(s)
whose descriptions, tags, and categories best match the goal.

Rules:
- Prefer skills whose description clearly fits the job.
- Tags are strong domain/technique signals (whatever each skill declares).
- Category is a soft bucket from each skill — use only categories listed in the
  catalog; do not invent category names.
- Prefer engagement skills over utility/platform helpers unless the job is about
  CLI, sandbox, MCP, or install work.
- NEVER pick a direct-answer skill when the user wants created artifacts:
  files, reports, charts, diagrams, HTML, PDF, images, or "create / generate /
  produce / build / make a …". Leave skills=[] (or project-manager for
  long multi-phase work) so the planner + tools run.
- For explicit plan-only requests ("write a plan", "/plan", "blueprint",
  "plan mode"): pick `blueprint` when it is in the catalog.
- For multi-format or multi-artifact deliverables: skills=[] or project-manager.
- For CLI / install / sandbox work, prefer dedicated tool skills; installs come from
  tools/catalog (worker provision). Ad-hoc shell uses the `run_cli` tool.
- For MCP / Model Context Protocol / mcpServers / "use this mcp" / npx|uvx *mcp*:
  prefer catalog skills that advertise mcp capabilities.
- You may pick 0–N skills. Empty list means no skill preload.
- Prefer the smallest set that covers the job (usually 1 skill).
- Do NOT invent skill names. Only use names from the catalog.

Reply with ONLY a JSON object:
{"skills":["name",...],"reason":"one short sentence"}
"""

    def __init__(
        self,
        *,
        registry: SkillRegistry | None = None,
        name_matcher: SkillNameMatcher | None = None,
    ) -> None:
        self._registry = registry or SkillRegistry.shared()
        self._name_matcher = name_matcher or SkillNameMatcher.shared()

    def looks_like_file_deliverable(self, description: str) -> bool:
        text = (description or "").strip()
        return bool(text and DELIVERABLE_RE.search(text))

    def _selectable(self) -> list[Skill]:
        return filter_skills(self._registry.get_registry().values(), jobable_only=True)

    def _strip_direct_answer_skills(
        self, names: list[str], *, by_name: dict[str, Skill] | None = None
    ) -> list[str]:
        index = by_name if by_name is not None else self._registry.get_registry()
        return [n for n in names if not getattr(index.get(n), "direct_answer", False)]

    def _catalog_text(self, skills: list[Skill] | None = None) -> str:
        return format_catalog(skills if skills is not None else self._selectable(), mode="llm")

    def _skills_with_lifecycle(self, lifecycle: str, skills: list[Skill]) -> list[str]:
        wanted = (lifecycle or "").strip().lower()
        return [
            s.name
            for s in sorted(skills, key=lambda s: s.name)
            if (s.lifecycle or "").strip().lower() == wanted
        ]

    def _mcp_skill_names(self, skills: list[Skill] | None = None) -> list[str]:
        pool = skills if skills is not None else self._selectable()
        return sorted((s.name for s in pool if s.is_mcp), key=str)

    def _named_if_selectable(self, name: str, by_name: dict[str, Skill]) -> list[str]:
        skill = by_name.get(name)
        if skill is not None and skill.jobable:
            return [name]
        return []

    def _match_named_in_text(
        self, description: str, *, valid: set[str]
    ) -> list[str]:
        """Return selectable skill ids named (or aliased) in ``description``."""
        alias_map = self._registry.skill_aliases()
        keys = set(valid) | set(alias_map.keys())
        matched: list[str] = []
        for name in self._name_matcher.find(description, keys):
            canonical = alias_map.get(name, name)
            if canonical in valid and canonical not in matched:
                matched.append(canonical)
        return matched

    def _parse_skills_json(self, raw: str, *, valid: set[str]) -> list[str]:

        text = extract_json(plain_text(raw))
        data = json.loads(text)
        names = data.get("skills") or []
        if isinstance(names, str):
            names = [names]
        out: list[str] = []
        for name in names:
            n = str(name or "").strip()
            if n in valid and n not in out:
                out.append(n)
        reason = str(data.get("reason") or "")[:160]
        if out or reason:
            logger.info("Skill AI inferred %s (%s)", out, reason)
        return out

    @staticmethod
    def _focus_tags_note(preferred_tags: list[str] | None) -> str:
        if not preferred_tags:
            return ""
        return (
            "\nProject focus tags (starting preference only — not exclusive; "
            "pick any catalog skill when the job needs it): "
            + ", ".join(preferred_tags)
            + "\n"
        )

    def infer_skills_with_llm(
        self,
        description: str,
        *,
        preferred_tags: list[str] | None = None,
        block_direct_answer: bool = False,
        skills: list[Skill] | None = None,
    ) -> list[str]:
        text = (description or "").strip()
        if not text:
            return []

        pool = skills if skills is not None else self._selectable()
        catalog = self._catalog_text(pool)
        if not catalog or catalog == "No skills installed.":
            return []
        valid = {s.name for s in pool}
        by_name = {s.name: s for s in pool}

        try:

            raw = chat_text(
                self.SYSTEM_PROMPT,
                (
                    "Selectable skill catalog:\n"
                    + catalog
                    + self._focus_tags_note(preferred_tags)
                    + "\n\nUser job:\n"
                    + text[:4000]
                    + '\n\nJSON only: {"skills":["..."],"reason":"..."}'
                ),
            )
            names = self._parse_skills_json(raw, valid=valid)
            if block_direct_answer or self.looks_like_file_deliverable(text):
                stripped = self._strip_direct_answer_skills(names, by_name=by_name)
                if stripped != names:
                    logger.info(
                        "Skill router stripped direct_answer skills: %s → %s",
                        names,
                        stripped,
                    )
                return stripped
            return names
        except Exception:
            logger.exception("Skill LLM inference failed; returning no skills")
            return []

    def resolve_default_skills(
        self,
        description: str,
        lifecycle: str = "auto",
        *,
        explicit: Iterable[str] | None = None,
        preferred_tags: list[str] | None = None,
        project: bool = False,
    ) -> list[str]:
        """Pick skills for a job. ``preferred_tags`` are soft focus hints only."""
        pool = self._selectable()
        by_name = {s.name: s for s in pool}
        valid = set(by_name)

        def finish(names: list[str]) -> list[str]:
            return self._strip_direct_answer_skills(names, by_name=by_name) if project else names

        names = [str(n).strip() for n in (explicit or []) if str(n).strip()]
        if names:
            return finish(names)

        lc = str(lifecycle or "auto").strip().lower()
        if lc == "continuous":
            continuous = self._skills_with_lifecycle("continuous", pool)
            if continuous:
                return finish(continuous[:1])

        matched = self._match_named_in_text(description, valid=valid)
        if matched:
            return finish(matched)

        if lc == "long" and not preferred_tags and not project:
            hit = self._named_if_selectable("project-manager", by_name)
            if hit:
                return finish(hit)

        if looks_like_mcp_request(description):
            mcp_names = self._mcp_skill_names(pool)
            if mcp_names:
                return finish(mcp_names[:1])

        inferred = self.infer_skills_with_llm(
            description,
            preferred_tags=preferred_tags,
            block_direct_answer=project or self.looks_like_file_deliverable(description),
            skills=pool,
        )
        if inferred:
            return finish(inferred)
        return []
