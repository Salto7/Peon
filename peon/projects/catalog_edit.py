"""Shared catalog source load/save for skills and tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from django.conf import settings

from orchestrator.tools.catalog import ToolCatalog


def load_skill_source(skill_id: str) -> dict[str, Any]:
    from peon.projects.platform_catalog import PlatformCatalog

    rec = PlatformCatalog.shared().get_skill(skill_id)
    if rec is None:
        raise FileNotFoundError(f"skill {skill_id!r} not found")
    source = str((rec.manifest or {}).get("_source") or "").strip()
    if not source:
        source = f"---\nname: {rec.skill_id}\ndescription: {rec.description}\n---\n\n{rec.body}\n"
    return {
        "kind": "skill",
        "id": rec.skill_id,
        "path": f"platform:{rec.skill_id}",
        "skill_md": source,
        "files": dict(rec.files),
        "bindings": [
            {"role_id": b.role_id, "score": b.score, "lead": b.lead}
            for b in rec.bindings
        ],
    }


def save_skill_source(
    skill_id: str,
    *,
    skill_md: str,
    files: dict[str, str] | None = None,
    bindings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from orchestrator.skills.providers.base import SkillId
    from orchestrator.toolsmith import lint_skill
    from peon.projects.platform_catalog import PlatformCatalog

    hinted = str(skill_id or "").strip()
    parsed = SkillId.parse(hinted) if hinted and hinted != "__new__" else None
    md = str(skill_md or "").strip()
    if not md:
        raise ValueError("skill_md is required")
    file_map = {str(k): str(v) for k, v in (files or {}).items()}
    import re

    matched = re.search(r"(?m)^name:\s*([A-Za-z0-9-]+)\s*$", md)
    lint_name = (matched.group(1).lower() if matched else "") or (
        parsed.name if parsed is not None else ""
    )
    checked: dict[str, Any] = {"compatible": True}
    if lint_name:
        try:
            checked = lint_skill(name=lint_name, skill_md=md, files=file_map)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
    if checked.get("compatible") is False:
        return {
            "ok": False,
            "error": "Skill failed lint — fix issues before saving.",
            "lint": {
                "compatible": False,
                "errors": checked.get("errors") or [],
                "warnings": checked.get("warnings") or [],
            },
        }
    rec = PlatformCatalog.shared().upsert_skill(
        skill_md=md,
        files=file_map,
        skill_id="" if hinted in {"", "__new__"} else hinted,
        bindings=bindings,
    )
    return {
        "ok": True,
        "kind": "skill",
        "id": rec.skill_id,
        "path": f"platform:{rec.skill_id}",
        "files": sorted(rec.files),
    }


def load_tool_source(tool_id: str) -> dict[str, Any]:
    tid = str(tool_id or "").strip()
    if not tid or "/" in tid or ".." in tid:
        raise ValueError("invalid tool id")
    catalog = Path(settings.TOOLS_CATALOG_DIR)
    path = catalog / f"{tid}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"tool {tid!r} not found")
    script_path = catalog / f"{tid}.sh"
    script = script_path.read_text(encoding="utf-8") if script_path.is_file() else ""
    return {
        "kind": "tool",
        "id": tid,
        "path": str(path),
        "yaml": path.read_text(encoding="utf-8"),
        "install_script": script,
        "script_path": str(script_path) if script else "",
    }


def save_tool_source(
    tool_id: str,
    *,
    yaml_text: str,
    install_script: str = "",
) -> dict[str, Any]:
    tid_hint = str(tool_id or "").strip()
    text = str(yaml_text or "").strip()
    if not text:
        raise ValueError("yaml is required")
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise ValueError("tool YAML must be a mapping")
    tid = str(raw.get("id") or tid_hint or "").strip()
    if not tid or "/" in tid or ".." in tid:
        raise ValueError("invalid tool id")
    catalog = Path(settings.TOOLS_CATALOG_DIR)
    catalog.mkdir(parents=True, exist_ok=True)
    path = catalog / f"{tid}.yaml"
    path.write_text(text.rstrip() + "\n", encoding="utf-8")
    script_path = catalog / f"{tid}.sh"
    script = str(install_script or "").strip()
    if script:
        script_path.write_text(script.rstrip() + "\n", encoding="utf-8")
    elif script_path.is_file():
        script_path.unlink()
    ToolCatalog.shared().invalidate()
    return {
        "ok": True,
        "kind": "tool",
        "id": tid,
        "path": str(path),
        "script_path": str(script_path) if script else "",
    }


def delete_tool_source(tool_id: str) -> None:
    tid = str(tool_id or "").strip()
    if not tid or "/" in tid or ".." in tid:
        raise ValueError("invalid tool id")
    catalog = Path(settings.TOOLS_CATALOG_DIR)
    path = catalog / f"{tid}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"tool {tid!r} not found")
    path.unlink()
    script_path = catalog / f"{tid}.sh"
    if script_path.is_file():
        script_path.unlink()
    ToolCatalog.shared().invalidate()
