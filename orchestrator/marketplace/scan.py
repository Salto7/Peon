"""Read-only discovery over a fetched marketplace source.

Every scanner here only *reads* files as text (``Path.read_text``,
``json.loads``, YAML/TOML parsing) — nothing from the source is ever
executed, imported as a Python module, or shell-invoked.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orchestrator.skills.common import parse_frontmatter_yaml
from orchestrator.skills.registry_scan import discover_skill_candidates

_AGENT_EXTS = (".toml", ".yaml", ".yml")
_MCP_MANIFEST_NAMES = {"manifest.json", "mcp.json", ".mcp.json"}


def scan_skills(root: Path) -> list[dict[str, Any]]:
    """Every ``SKILL.md`` pack found, with its (pre-namespacing) frontmatter."""
    out: list[dict[str, Any]] = []
    for candidate in discover_skill_candidates(root, source="marketplace", skip_helpers=True):
        try:
            text = candidate.manifest.read_text(encoding="utf-8")
        except OSError:
            continue
        parsed = parse_frontmatter_yaml(text)
        fm = parsed[0] if parsed else {}
        name = str(fm.get("name") or candidate.root.name).strip().lower()
        out.append(
            {
                "name": name,
                "namespace": candidate.namespace,
                "description": str(fm.get("description") or "").strip(),
                "path": str(candidate.root.relative_to(root)),
            }
        )
    return out


def _iter_agent_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _AGENT_EXTS:
            continue
        if "agents" not in {p.lower() for p in path.parent.parts}:
            continue
        found.append(path)
    return found


def scan_agents(root: Path) -> list[dict[str, Any]]:
    """Codex/OpenAI-style subagent configs (``agents/*.toml`` or ``.yaml``).

    These have no Peon equivalent to map onto automatically; the caller
    stages each as a draft role proposal for a human to approve.
    """
    import tomllib

    import yaml

    out: list[dict[str, Any]] = []
    for path in _iter_agent_files(root):
        try:
            raw = path.read_text(encoding="utf-8")
            data = tomllib.loads(raw) if path.suffix.lower() == ".toml" else (yaml.safe_load(raw) or {})
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        description = str(data.get("description") or "").strip()
        instructions = str(
            data.get("developer_instructions") or data.get("instructions") or ""
        ).strip()
        if not description and not instructions:
            continue
        name = str(data.get("name") or path.stem).strip().lower()
        out.append(
            {
                "name": name,
                "description": description,
                "instructions": instructions[:4000],
                "path": str(path.relative_to(root)),
            }
        )
    return out


def scan_mcp_pointers(root: Path) -> list[dict[str, Any]]:
    """MCP manifests that *point at* an external server repo rather than
    being runnable server code themselves (no ``command``). A manifest with
    ``command`` is real, runnable server code — ``install_mcp_source`` (the
    existing installer) already handles that case directly; this scanner
    only surfaces the pointer so the operator can one-click install the
    repo it names, through that same existing installer.
    """
    out: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.lower() not in _MCP_MANIFEST_NAMES:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or data.get("command"):
            continue
        source = data.get("source") if isinstance(data.get("source"), dict) else {}
        repo = str(source.get("default") or data.get("repo") or "").strip()
        if not repo:
            continue
        out.append(
            {
                "name": str(data.get("name") or path.parent.name).strip().lower(),
                "repo_url": repo,
                "secrets": [str(s) for s in (data.get("secrets") or [])],
                "configuration": [str(c) for c in (data.get("configuration") or [])],
                "path": str(path.relative_to(root)),
            }
        )
    return out


def scan_all(root: Path) -> dict[str, list[dict[str, Any]]]:
    return {
        "skills": scan_skills(root),
        "agents": scan_agents(root),
        "mcp_pointers": scan_mcp_pointers(root),
    }
