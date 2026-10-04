"""Shared pack helpers for ``roles/``.

Peon roles use ``ROLE.yaml`` + ``KNOWLEDGE.md``; executables live under
``assets/`` (no ``scripts/`` directory). ``resolve_resource`` accepts
``scripts/`` paths as aliases for ``assets/``.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.utils.paths import normalize_rel_path, safe_join

ROLE_RESOURCE_ROOTS = ("assets", "references")
# Accept scripts/ as an alias when resolving legacy relative paths.
_RESOLVE_ROOTS = ("assets", "references", "scripts")


def list_resource_files(
    pack_dir: Path, *, roots: tuple[str, ...] | None = None
) -> list[str]:
    """Relative paths under the given resource roots (default: role layout)."""
    out: list[str] = []
    root = Path(pack_dir)
    if not root.is_dir():
        return out
    for root_name in roots or ROLE_RESOURCE_ROOTS:
        base = root / root_name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.name.startswith("."):
                continue
            if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            out.append(path.relative_to(root).as_posix())
    return out


def list_role_resource_files(pack_dir: Path) -> list[str]:
    """Role packs: ``assets/`` + ``references/`` only."""
    return list_resource_files(pack_dir, roots=ROLE_RESOURCE_ROOTS)


def resolve_resource(pack_dir: Path, relative_path: str) -> Path | None:
    """Resolve a pack-relative resource path (assets/…, references/…)."""
    rel = normalize_rel_path(relative_path)
    if rel is None:
        return None
    base = Path(pack_dir).resolve()
    candidates = [rel]
    for root_name in _RESOLVE_ROOTS:
        prefix = f"{root_name}/"
        if rel.startswith(prefix):
            candidates.append(f"{root_name}/{rel[len(prefix):]}")
        else:
            candidates.append(f"{root_name}/{rel}")
    # Roles store executables under assets/; accept scripts/ aliases.
    if rel.startswith("assets/"):
        candidates.append(f"scripts/{rel.removeprefix('assets/')}")
    if rel.startswith("scripts/"):
        candidates.append(f"assets/{rel.removeprefix('scripts/')}")
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = safe_join(base, candidate)
        if resolved is None or resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return resolved
    return None


__all__ = [
    "ROLE_RESOURCE_ROOTS",
    "list_resource_files",
    "list_role_resource_files",
    "resolve_resource",
]
