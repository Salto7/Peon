"""Parse agentskills.io SKILL.md manifests."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from orchestrator.skills.tags import TagNormalizer
from orchestrator.skills.common import (
    FRONTMATTER_RE,
    KEY_LINE_RE,
    coerce_jobable,
    coerce_lifecycle,
    normalize_category,
)
from orchestrator.utils.service import SharedServiceBase
from orchestrator.utils.strings import as_bool, as_str_list

logger = logging.getLogger(__name__)


class SkillParser(SharedServiceBase):
    """Parse agentskills.io SKILL.md (product fields under metadata)."""

    def load_frontmatter(self, raw: str) -> dict[str, Any]:
        try:
            data = yaml.safe_load(raw)
            if isinstance(data, dict):
                return data
            if data is None:
                return {}
        except yaml.YAMLError as exc:
            logger.warning("SKILL.md frontmatter YAML parse failed (%s); using lenient parser", exc)
        return self._lenient_frontmatter(raw)

    def _lenient_frontmatter(self, raw: str) -> dict[str, Any]:
        fixed_lines: list[str] = []
        for line in (raw or "").splitlines():
            m = KEY_LINE_RE.match(line)
            if (
                m
                and m.group(2)
                and not m.group(2)[:1].isspace()
                and m.group(2)[:1] not in {"[", "{", "|", ">"}
            ):
                fixed_lines.append(f"{m.group(1)}: {m.group(2)}")
            else:
                fixed_lines.append(line)
        try:
            data = yaml.safe_load("\n".join(fixed_lines))
            if isinstance(data, dict):
                return data
        except yaml.YAMLError:
            pass
        return {}

    @staticmethod
    def _layers(frontmatter: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return (metadata, legacy orchestrator) maps."""
        meta = frontmatter.get("metadata") if isinstance(frontmatter.get("metadata"), dict) else {}
        orch = meta.get("orchestrator") if isinstance(meta.get("orchestrator"), dict) else {}
        return meta, orch

    @staticmethod
    def _get(frontmatter: dict[str, Any], meta: dict[str, Any], orch: dict[str, Any], *keys: str):
        """Prefer top-level (legacy) → metadata → metadata.orchestrator."""
        for key in keys:
            if key in frontmatter and frontmatter[key] is not None:
                return frontmatter[key]
        for key in keys:
            if key in meta and meta[key] is not None:
                return meta[key]
        for key in keys:
            if key in orch and orch[key] is not None:
                return orch[key]
        return None

    @staticmethod
    def _tools_from(frontmatter: dict[str, Any], meta: dict[str, Any], orch: dict[str, Any]) -> list[str]:
        raw = (
            frontmatter.get("allowed-tools")
            or frontmatter.get("allowed_tools")
            or SkillParser._get(frontmatter, meta, orch, "capabilities", "tools")
        )
        if isinstance(raw, str):
            return [p for p in raw.split() if p]
        return as_str_list(raw)

    def _meta_from_frontmatter(
        self,
        frontmatter: dict[str, Any],
        body: str,
        *,
        default_name: str,
        path: Path | None = None,
    ) -> dict[str, Any]:
        meta, orch = self._layers(frontmatter)
        name = frontmatter.get("name") or default_name
        tools = self._tools_from(frontmatter, meta, orch)
        tags = TagNormalizer.normalize(
            self._get(frontmatter, meta, orch, "tags")
        )
        toolkit = as_str_list(
            self._get(frontmatter, meta, orch, "requires_clis", "toolkit")
        )
        from orchestrator.skills.eligibility import parse_id_list

        suggested = parse_id_list(
            self._get(frontmatter, meta, orch, "suggested_tools", "tools_include")
        )
        excluded = parse_id_list(
            self._get(frontmatter, meta, orch, "excluded_tools", "tools_exclude")
        )
        compatibility = str(frontmatter.get("compatibility") or "").strip()
        if not toolkit and compatibility.lower().startswith("requires "):
            # Only treat short comma/space package lists as toolkit — not prose.
            rest = compatibility[9:].strip()
            if rest and len(rest) <= 120 and " when " not in rest.lower():
                toolkit = as_str_list(rest)

        category = normalize_category(
            self._get(frontmatter, meta, orch, "category"),
        )
        jobable = coerce_jobable(
            name=str(name),
            category=category,
            raw=self._get(frontmatter, meta, orch, "jobable"),
        )

        mcp_raw = self._get(frontmatter, meta, orch, "mcp") or []
        result: dict[str, Any] = {
            "name": name,
            "description": frontmatter.get("description", "") or "",
            "instructions": body.strip(),
            "license": str(frontmatter.get("license") or "").strip(),
            "compatibility": compatibility,
            "tools": tools,
            "lifecycle": self.coerce_lifecycle(self._get(frontmatter, meta, orch, "lifecycle")),
            "max_iterations": self._get(frontmatter, meta, orch, "max_iterations"),
            "category": category,
            "tags": tags,
            "toolkit": toolkit,
            "suggested_tools": suggested,
            "excluded_tools": excluded,
            "aliases": as_str_list(self._get(frontmatter, meta, orch, "aliases")),
            "version": self._get(frontmatter, meta, orch, "version") or frontmatter.get("version"),
            "jobable": jobable,
            "manually_created": as_bool(
                self._get(frontmatter, meta, orch, "manually_created"), default=False
            ),
            "direct_answer": as_bool(
                self._get(frontmatter, meta, orch, "direct_answer"), default=False
            ),
            "protected": as_bool(self._get(frontmatter, meta, orch, "protected"), default=False),
            "mcp": mcp_raw if isinstance(mcp_raw, (list, dict)) else [],
        }
        if path is not None:
            result["manifest_path"] = path
        return result

    def parse_skill_md(self, path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8")
        match = FRONTMATTER_RE.match(text)
        if not match:
            return self._empty_meta(path.stem, text.strip(), path)
        return self._meta_from_frontmatter(
            self.load_frontmatter(match.group(1)),
            match.group(2),
            default_name=path.stem,
            path=path,
        )

    def coerce_lifecycle(self, value: Any, *, default: str = "short") -> str:
        return coerce_lifecycle(value, default=default, allow_auto=False)

    def _empty_meta(self, name: str, body: str, path: Path | None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": name,
            "description": "",
            "instructions": body,
            "license": "",
            "compatibility": "",
            "tools": [],
            "lifecycle": "short",
            "max_iterations": None,
            "category": normalize_category(None),
            "tags": [],
            "toolkit": [],
            "suggested_tools": [],
            "excluded_tools": [],
            "aliases": [],
            "jobable": True,
            "manually_created": False,
            "direct_answer": False,
            "protected": False,
            "mcp": [],
        }
        if path is not None:
            result["manifest_path"] = path
        return result
