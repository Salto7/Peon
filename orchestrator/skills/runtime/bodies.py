"""Claude progressive skill tools (load / read / run with argv)."""

from __future__ import annotations

import json

from orchestrator.skills.providers.base import CLAUDE_PROVIDER, SkillDocument
from orchestrator.skills.registry import SkillRegistry
from orchestrator.skills.runtime.sandbox import SandboxTools, job_role_id


def resolve_skill_document(name: str) -> SkillDocument | None:
    """Bare names prefer peon; if missing, try ``claude/<name>``."""
    reg = SkillRegistry.shared()
    doc = reg.get(name)
    if doc is None and "/" not in str(name or ""):
        doc = reg.get(f"{CLAUDE_PROVIDER}/{name}")
    return doc


def _args(raw: object) -> list[str] | str:
    if isinstance(raw, list):
        if all(isinstance(item, str) for item in raw):
            return list(raw)
        return "Error: args must be a list of strings."
    text = str(raw or "").strip()
    if not text:
        return []
    if not text.startswith("["):
        return "Error: args must be a JSON list of strings."
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return "Error: args must be a JSON list of strings."
    if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
        return "Error: args must be a JSON list of strings."
    return data


def load_skill_body(name: str) -> str:
    doc = resolve_skill_document(name)
    if doc is None:
        return f"Error: unknown skill {name!r}."
    blocked = doc.access_error(role_id=job_role_id(required=False))
    if blocked:
        return blocked
    parts = [doc.instructions.strip() or doc.description.strip() or doc.id]
    if doc.setup is not None:
        parts.append(f"Setup: {doc.setup.kind} ({doc.setup.cwd}).")
    return "\n\n".join(part for part in parts if part)


def read_skill_resource_body(name: str, path: str) -> str:
    doc = resolve_skill_document(name)
    if doc is None:
        return f"Error: unknown skill {name!r}."
    blocked = doc.access_error(role_id=job_role_id(required=False))
    if blocked:
        return blocked
    try:
        text = SkillRegistry.shared().load_text(doc.id, path)
    except FileNotFoundError:
        return f"Error: {doc.id} has no file {path!r}."
    except OSError as exc:
        return f"Error: {doc.id} could not read {path!r} ({exc})."
    if len(text) > 200_000:
        return "Error: resource is too large to return."
    return text


def run_skill_script_body(
    name: str,
    relative_path: str,
    args: object = "",
) -> str:
    doc = resolve_skill_document(name)
    if doc is None:
        return f"Error: unknown skill {name!r}."
    blocked = doc.access_error(role_id=job_role_id(required=False))
    if blocked:
        return blocked
    parsed = _args(args)
    if isinstance(parsed, str):
        return parsed
    return SandboxTools().script(doc.id, relative_path, parsed)
