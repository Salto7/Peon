"""Role draft helpers for Learn/OpenCode (ROLE.yaml packs under roles/)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import yaml

from orchestrator.crew.roles.registry import RoleRegistry, load_role_file
from orchestrator.utils.paths import write_rel_files
from orchestrator.utils.strings import as_str_list, is_kebab_slug, to_kebab_slug


def valid_role_name(name: str) -> bool:
    return is_kebab_slug(name)


def role_prompt(role_id: str = "code-writer", *, task: str = "role") -> str:
    """Load an authoring prompt from the role pack (ROLE.yaml ``authoring.tasks``)."""
    role = RoleRegistry.shared().get(role_id)
    if role is None:
        raise FileNotFoundError(f"authoring role {role_id!r} not found")
    return role.authoring_prompt_text(task)


def lint_role_pack(
    *,
    name: str,
    role_yaml: str,
    files: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Validate ROLE.yaml (+ optional files) by loading via the role loader."""
    errors: list[str] = []
    warnings: list[str] = []
    rid = (name or "").strip()
    if not valid_role_name(rid):
        errors.append("role id must be lowercase kebab-case")
    text = (role_yaml or "").strip()
    if not text:
        errors.append("ROLE.yaml is empty")
        return {"compatible": False, "errors": errors, "warnings": warnings}

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        errors.append(f"ROLE.yaml is not valid YAML: {exc}")
        return {"compatible": False, "errors": errors, "warnings": warnings}
    if not isinstance(data, dict):
        errors.append("ROLE.yaml must be a mapping")
        return {"compatible": False, "errors": errors, "warnings": warnings}

    yaml_id = str(data.get("id") or "").strip()
    if yaml_id and yaml_id != rid:
        errors.append(f"ROLE.yaml id {yaml_id!r} must match directory name {rid!r}")
    if not str(data.get("goal") or "").strip():
        errors.append("goal is required")
    if not str(data.get("crew_role") or data.get("label") or "").strip():
        warnings.append("crew_role/label missing — will default to id")

    tools = data.get("tools")
    if tools is not None and not isinstance(tools, (list, tuple, str)):
        errors.append("tools must be a list or string")

    try:
        with tempfile.TemporaryDirectory(prefix="peon-role-lint-") as tmp:
            root = Path(tmp) / rid
            root.mkdir(parents=True)
            (root / "ROLE.yaml").write_text(text.rstrip() + "\n", encoding="utf-8")
            try:
                write_rel_files(root, files or {}, skip={"ROLE.yaml", "ROLE.yml"})
            except ValueError as exc:
                errors.append(str(exc))
            if not errors:
                load_role_file(root / "ROLE.yaml")
    except Exception as exc:
        errors.append(f"role load failed: {exc}")

    return {
        "compatible": not errors,
        "errors": errors,
        "warnings": warnings,
    }


def finalize_role_payload(
    payload: dict[str, Any],
    *,
    prompt: str = "",
) -> tuple[str, str, dict[str, str], list[str], dict[str, Any]]:
    """Normalize OpenCode/role JSON into (name, role_yaml, files, suggested_tools, lint)."""
    del prompt
    if not isinstance(payload, dict):
        raise RuntimeError("role authoring returned non-object JSON")

    files_raw = payload.get("files") or {}
    if not isinstance(files_raw, dict):
        files_raw = {}
    files = {str(k): str(v) for k, v in files_raw.items()}

    role_yaml = str(
        payload.get("role_yaml")
        or payload.get("ROLE.yaml")
        or files.get("ROLE.yaml")
        or ""
    ).strip()
    if not role_yaml and files:
        for key, val in list(files.items()):
            if key.endswith("ROLE.yaml") or key.endswith("ROLE.yml"):
                role_yaml = str(val).strip()
                break

    name = str(payload.get("name") or payload.get("id") or "").strip()
    if not name and role_yaml:
        try:
            parsed = yaml.safe_load(role_yaml) or {}
            if isinstance(parsed, dict):
                name = str(parsed.get("id") or "").strip()
        except yaml.YAMLError:
            pass
    if not name:
        raise RuntimeError("role draft missing name/id")
    if not valid_role_name(name):
        slug = to_kebab_slug(name)
        if not valid_role_name(slug):
            raise RuntimeError(f"invalid role name {name!r}")
        name = slug

    if not role_yaml:
        raise RuntimeError("role draft missing ROLE.yaml contents")

    try:
        data = yaml.safe_load(role_yaml) or {}
        if isinstance(data, dict):
            data["id"] = name
            role_yaml = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).strip()
    except yaml.YAMLError:
        pass

    files.pop("ROLE.yaml", None)
    files.pop("ROLE.yml", None)
    knowledge = str(
        payload.get("knowledge_md") or files.get("KNOWLEDGE.md") or ""
    ).strip()
    if knowledge:
        files["KNOWLEDGE.md"] = knowledge

    suggested_list = as_str_list(payload.get("suggested_tools") or [])
    lint = lint_role_pack(name=name, role_yaml=role_yaml, files=files)
    return name, role_yaml, files, suggested_list, lint


def role_result_dict(
    *,
    name: str,
    role_yaml: str,
    files: dict[str, str],
    notes: str = "",
    suggested: list[str] | None = None,
    lint: dict[str, Any] | None = None,
    author: str = "",
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": name,
        "role_yaml": role_yaml,
        "files": files,
        "notes": (notes or "").strip(),
        "suggested_tools": list(suggested or []),
        "lint": lint
        or lint_role_pack(name=name, role_yaml=role_yaml, files=files),
    }
    if author:
        out["author"] = author
    return out


def assemble_role_result(
    payload: dict[str, Any],
    *,
    prompt: str = "",
    author: str = "",
    tool_suggestion: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """finalize_role_payload → role_result_dict (+ optional tool_suggestion)."""
    if not isinstance(payload, dict):
        raise RuntimeError("authoring role returned non-object JSON")
    name, role_yaml, files, suggested, lint = finalize_role_payload(
        payload, prompt=prompt
    )
    out = role_result_dict(
        name=name,
        role_yaml=role_yaml,
        files=files,
        notes=str(payload.get("notes") or "").strip(),
        suggested=suggested,
        lint=lint,
        author=author,
    )
    if tool_suggestion is not None:
        out["tool_suggestion"] = tool_suggestion
    return out
