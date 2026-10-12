"""Shared catalog source load/save for skills and tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from django.conf import settings

from orchestrator.crew.roles.registry import RoleRegistry
from orchestrator.skills.registry import SkillRegistry
from orchestrator.tools.catalog import ToolCatalog


def _safe_rel(rel: str) -> str:
    rel_s = str(rel or "").replace("\\", "/").lstrip("/")
    if not rel_s or ".." in rel_s.split("/"):
        raise ValueError(f"unsafe path {rel!r}")
    return rel_s


def _native_skill_dir(name: str) -> Path | None:
    root = Path(settings.SKILLS_DIR) / name
    if (root / "SKILL.md").is_file():
        return root
    return None


def _imported_skill_dir(name: str) -> Path | None:
    root = Path(settings.SKILLS_IMPORTED_DIR) / name
    if (root / "SKILL.md").is_file():
        return root
    return None


def load_skill_source(skill_id: str) -> dict[str, Any]:
    from orchestrator.skills.providers.base import CLAUDE_PROVIDER, SkillId

    parsed = SkillId.parse(skill_id)
    if parsed is None:
        raise ValueError("invalid skill id")
    if parsed.provider == CLAUDE_PROVIDER:
        root = _imported_skill_dir(parsed.name)
        public_id = parsed.public
    else:
        native = _native_skill_dir(parsed.name)
        root = native or _imported_skill_dir(parsed.name)
        public_id = parsed.name if native is not None else f"claude/{parsed.name}"
    if root is None:
        raise FileNotFoundError(f"skill {skill_id!r} not found")
    sid = public_id
    skill_md = root / "SKILL.md"
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel == "SKILL.md" or path.name.startswith("."):
            continue
        try:
            files[rel] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            files[rel] = f"<<binary omitted: {rel}>>"
    return {
        "kind": "skill",
        "id": sid,
        "path": str(root),
        "skill_md": skill_md.read_text(encoding="utf-8"),
        "files": files,
    }


def save_skill_source(
    skill_id: str,
    *,
    skill_md: str,
    files: dict[str, str] | None = None,
) -> dict[str, Any]:
    from orchestrator.learn import LearnAuthoring
    from orchestrator.learn.draft import draft_binary
    from orchestrator.skills.author import save_claude_skill
    from orchestrator.skills.providers.base import CLAUDE_PROVIDER, SkillId

    parsed = SkillId.parse(skill_id)
    if parsed is None:
        raise ValueError("invalid skill id")
    md = str(skill_md or "").strip()
    if not md:
        raise ValueError("skill_md is required")
    file_map = {str(k): str(v) for k, v in (files or {}).items()}
    native = None if parsed.provider == CLAUDE_PROVIDER else _native_skill_dir(parsed.name)
    if native is None:
        saved = save_claude_skill(
            name=parsed.name,
            skill_md=md,
            files=file_map,
            source="catalog",
            binary=draft_binary(md),
        )
        if saved.get("ok"):
            RoleRegistry.shared().invalidate()
        return saved
    sid = parsed.name
    checked = LearnAuthoring.shared().lint_skill(name=sid, skill_md=md, files=file_map)
    if not checked.get("compatible"):
        return {
            "ok": False,
            "error": "Skill failed lint — fix issues before saving.",
            "lint": {
                "compatible": False,
                "errors": checked.get("errors") or [],
                "warnings": checked.get("warnings") or [],
            },
        }
    root = Path(settings.SKILLS_DIR) / sid
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(md.rstrip() + "\n", encoding="utf-8")
    written = ["SKILL.md"]
    for rel, content in file_map.items():
        rel_s = _safe_rel(rel)
        if content.startswith("<<binary omitted:"):
            continue
        path = root / rel_s
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(content).rstrip() + "\n", encoding="utf-8")
        written.append(rel_s)
    try:
        SkillRegistry.shared().reload_skills()
    except Exception:
        pass
    RoleRegistry.shared().invalidate()
    return {"ok": True, "kind": "skill", "id": sid, "path": str(root), "files": written}


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
