"""Role pack resources — same layout as skills (scripts/, assets/, references/)."""

from __future__ import annotations

from pathlib import Path

# Keep in sync with orchestrator.skills.common.RESOURCE_ROOTS.
RESOURCE_ROOTS = ("scripts", "assets", "references")


def list_resource_files(role_dir: Path) -> list[str]:
    """Relative paths under scripts/, assets/, references/."""
    out: list[str] = []
    root = Path(role_dir)
    if not root.is_dir():
        return out
    for root_name in RESOURCE_ROOTS:
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


def resolve_resource(role_dir: Path, relative_path: str) -> Path | None:
    """Resolve a role-relative resource path (scripts/…, assets/…, references/…)."""
    if not relative_path or relative_path.startswith("/"):
        return None
    rel = relative_path.lstrip("./")
    base = Path(role_dir).resolve()
    candidates = [base / rel]
    for root_name in RESOURCE_ROOTS:
        prefix = f"{root_name}/"
        if rel.startswith(prefix):
            candidates.append(base / root_name / rel[len(prefix) :])
        else:
            candidates.append(base / root_name / rel)
    if rel.startswith("assets/"):
        candidates.append(base / "scripts" / rel.removeprefix("assets/"))
    if rel.startswith("scripts/"):
        candidates.append(base / "assets" / rel.removeprefix("scripts/"))
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if str(resolved).startswith(str(base)) and resolved.is_file():
            return resolved
    return None
