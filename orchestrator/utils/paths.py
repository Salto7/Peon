"""Path, workspace, and RoE formatting helpers."""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any, Iterable

from orchestrator.config import get_config

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_LAYOUT_SUBDIRS = ("plans", "inputs", "findings", "workspace")


def normalize_rel_path(relative_path: str | None) -> str | None:
    """Normalize a pack-relative path; return None if empty or escapes with ``..``."""
    rel = (relative_path or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in Path(rel).parts:
        return None
    return rel


def safe_join(root: Path, relative_path: str | None) -> Path | None:
    """Join ``relative_path`` under ``root``; None if escape or empty."""
    rel = normalize_rel_path(relative_path)
    if rel is None:
        return None
    base = Path(root).resolve()
    target = (base / rel).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return None
    return target


def write_rel_files(
    root: Path,
    files: dict[str, str],
    *,
    skip: set[str] | frozenset[str] = frozenset(),
) -> list[str]:
    """Write relative text files under ``root``; return written relative paths."""
    written: list[str] = []
    for rel, content in (files or {}).items():
        rel_s = normalize_rel_path(str(rel))
        if not rel_s or rel_s in skip:
            if rel_s is None and str(rel or "").strip():
                raise ValueError(f"unsafe file path {rel!r}")
            continue
        path = safe_join(root, rel_s)
        if path is None:
            raise ValueError(f"unsafe file path {rel!r}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(content).rstrip() + "\n", encoding="utf-8")
        written.append(rel_s)
    return written


def absolutize_relative_paths(command: str, workdir: Path) -> str:
    """Rewrite relative path-like tokens to absolute paths under workdir."""
    if not command:
        return command
    try:
        tokens = shlex.split(command)
    except ValueError:
        return command
    root = workdir.resolve()
    out: list[str] = []
    changed = False
    for token in tokens:
        raw = token.strip().strip("'\"")
        path = Path(raw)
        if (
            not raw
            or raw.startswith("-")
            or path.is_absolute()
            or ".." in path.parts
            or len(path.parts) < 2
        ):
            out.append(token)
            continue
        target = (root / path).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            out.append(token)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        out.append(shlex.quote(str(target)))
        changed = True
    return " ".join(out) if changed else command


def workspaces_root() -> Path:
    root = Path(get_config().workspaces_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_workspace_key(key: str) -> str:
    raw = (key or "").strip().replace("/", "-").replace("\\", "-")
    cleaned = _SAFE.sub("-", raw).strip(".-") or "workspace"
    return cleaned[:64]


def job_dir(job_id: str) -> Path:
    root = workspaces_root()
    path = (root / safe_workspace_key(str(job_id))).resolve()
    path.relative_to(root)
    return path


def plans_dir(job_id: str) -> Path:
    return job_dir(job_id) / "plans"


def ensure_workspace_layout(root: Path) -> Path:
    """Create standard plans/inputs/findings/workspace children under ``root``."""
    path = Path(root)
    for sub in _LAYOUT_SUBDIRS:
        (path / sub).mkdir(parents=True, exist_ok=True)
    return path


def provision_job_workspace(job_id: str) -> Path:
    return ensure_workspace_layout(job_dir(job_id))


def format_target_line(item: Any) -> str:
    """One asset as ``type:value`` (or bare value when type is other/empty)."""
    if isinstance(item, dict):
        typ = str(item.get("type") or "other").strip() or "other"
        value = str(item.get("value") or "").strip()
        if not value:
            return ""
        return value if typ in {"", "other"} else f"{typ}:{value}"
    return str(item or "").strip()


def format_target_lines(items: Iterable[Any] | None) -> list[str]:
    return [line for x in (items or []) if (line := format_target_line(x))]


def join_target_lines(
    items: Iterable[Any] | None, *, empty: str = "(empty)"
) -> str:
    lines = format_target_lines(items)
    return ", ".join(lines) if lines else empty


def format_roe_block(roe: Any) -> str:
    """Multi-line RoE summary for planner / agent prompts."""
    if roe is None:
        return "RoE: (missing — do not invent targets; blueprint/passive only)"
    parts = [
        f"In-scope (authorized values; type optional hint): "
        f"{join_target_lines(roe.in_scope)}",
        f"Exclusions: {join_target_lines(roe.exclusions, empty='(none)')}",
    ]
    seed = getattr(roe, "seed", None) or []
    if seed:
        seed_line = join_target_lines(seed, empty="")
        if seed_line:
            parts.append(f"Seed / intent (not attack scope): {seed_line}")
    if getattr(roe, "authorization_note", None):
        parts.append(f"Authorization: {str(roe.authorization_note).strip()}")
    if getattr(roe, "testing_window_notes", None):
        parts.append(f"Window: {str(roe.testing_window_notes).strip()}")
    if getattr(roe, "abort_triggers", None):
        parts.append(f"Abort: {str(roe.abort_triggers).strip()}")
    return "RoE:\n- " + "\n- ".join(parts)
