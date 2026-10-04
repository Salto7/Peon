"""Load ``ROLE.yaml`` packs from the roles directory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from orchestrator.crew.roles.model import RoleSpec
from orchestrator.crew.roles.resources import list_resource_files


def _str_list(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        item = raw.strip()
        return (item,) if item else ()
    if isinstance(raw, (list, tuple)):
        out: list[str] = []
        for x in raw:
            s = str(x or "").strip()
            if s:
                out.append(s)
        return tuple(out)
    return ()


def load_role_file(path: Path) -> RoleSpec:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"ROLE.yaml must be a mapping: {path}")
    root = path.parent
    rid = str(data.get("id") or root.name).strip()
    if not rid:
        raise ValueError(f"role id missing: {path}")
    policy = data.get("tool_policy") if isinstance(data.get("tool_policy"), dict) else {}
    hierarchy = data.get("hierarchy") if isinstance(data.get("hierarchy"), dict) else {}
    label = str(data.get("label") or rid).strip()
    crew_role = str(data.get("crew_role") or label).strip()
    caps = _str_list(data.get("capabilities"))
    mode = str(data.get("mode") or "").strip().lower()
    if not mode:
        mode = (
            "authoring"
            if {c.lower() for c in caps} & {"authoring", "draft", "learn"}
            else "engagement"
        )
    assets = _str_list(data.get("assets"))
    if not assets:
        assets = tuple(list_resource_files(root))
    return RoleSpec(
        id=rid,
        label=label,
        crew_role=crew_role,
        goal=str(data.get("goal") or "").strip(),
        backstory=str(data.get("backstory") or "").strip(),
        tools=_str_list(data.get("tools")),
        allow_binaries=_str_list(policy.get("allow_binaries")),
        reports_to=str(hierarchy.get("reports_to") or data.get("reports_to") or "").strip(),
        capabilities=caps,
        knowledge_files=_str_list(data.get("knowledge") or data.get("knowledge_files")),
        assets=assets,
        mode=mode,
        requires_roe=bool(data.get("requires_roe", False)),
        allow_delegation=bool(data.get("allow_delegation", False)),
        reasoning=bool(data.get("reasoning", False)),
        max_iter=max(1, int(data.get("max_iter") or 20)),
        root=root,
    )


def discover_role_files(roles_dir: Path) -> list[Path]:
    root = Path(roles_dir)
    if not root.is_dir():
        return []
    found: list[Path] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        for name in ("ROLE.yaml", "ROLE.yml"):
            path = child / name
            if path.is_file():
                found.append(path)
                break
    return found
