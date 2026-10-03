"""Path helpers for shell/skill command rewriting."""

from __future__ import annotations

import shlex
from pathlib import Path


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
