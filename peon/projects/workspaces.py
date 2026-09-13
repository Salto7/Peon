"""Per-project filesystem workspace roots (isolated under PROJECT_WORKSPACES_DIR)."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def workspaces_root() -> Path:
    root = Path(
        getattr(settings, "PROJECT_WORKSPACES_DIR", "project_workspaces")
    ).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_workspace_key(key: str) -> str:
    raw = (key or "").strip().replace("/", "-").replace("\\", "-")
    cleaned = _SAFE.sub("-", raw).strip(".-") or "workspace"
    return cleaned[:64]


def project_workspace_dir(project_id: str, *, create: bool = True) -> Path:
    """Return ``PROJECT_WORKSPACES_DIR / <project_id>`` (only that project)."""
    root = workspaces_root()
    key = safe_workspace_key(str(project_id))
    path = (root / key).resolve()
    path.relative_to(root)  # raises if escaped
    if create:
        for sub in ("plans", "inputs", "findings", "workspace"):
            (path / sub).mkdir(parents=True, exist_ok=True)
    return path


def resolve_job_workspace(job) -> Path:
    """Project-bound jobs share the project folder; orphan jobs use workspace_id."""
    pid = getattr(job, "project_id", None)
    if pid:
        path = project_workspace_dir(str(pid))
        wid = safe_workspace_key(str(pid))
        if getattr(job, "workspace_id", None) != wid:
            job.workspace_id = wid
            try:
                job.save(update_fields=["workspace_id", "updated_at"])
            except Exception:
                pass
        return path
    wid = safe_workspace_key(
        (getattr(job, "workspace_id", None) or "").strip() or f"job-{job.id}"
    )
    if getattr(job, "workspace_id", None) != wid:
        job.workspace_id = wid
        try:
            job.save(update_fields=["workspace_id", "updated_at"])
        except Exception:
            pass
    return project_workspace_dir(wid)


def remove_project_workspace(project_id: str) -> dict:
    """Delete the on-disk project workspace tree (best-effort)."""
    try:
        path = project_workspace_dir(project_id, create=False)
    except ValueError:
        return {"action": "skipped", "reason": "unsafe id"}
    if not path.exists():
        return {"action": "missing", "path": str(path)}
    shutil.rmtree(path, ignore_errors=True)
    return {"action": "removed", "path": str(path)}


# --- Report discovery ---

_MD_SUFFIXES = {".md", ".markdown"}
# Prefer curated findings, then plan, then other markdown under findings/.
_SCAN_GLOBS = (
    ("findings/report.md", "final"),
    ("findings/*.md", "curated"),
    ("plans/latest.md", "plan"),
)


@dataclass(frozen=True)
class ReportEntry:
    name: str
    relative_path: str
    size: int
    modified_at: datetime | None
    kind: str  # final | curated | plan
    is_markdown: bool

    @property
    def display_name(self) -> str:
        if self.kind == "final":
            return "Final report"
        if self.kind == "plan":
            return "Project plan"
        return self.name


def resolve_report_path(project_id: str, relative_path: str) -> Path | None:
    """Resolve ``relative_path`` under the project workspace (no path escape)."""
    rel = (relative_path or "").lstrip("/").replace("\\", "/")
    if not rel or ".." in Path(rel).parts:
        return None
    try:
        root = project_workspace_dir(str(project_id), create=False).resolve()
    except ValueError:
        return None
    if not root.is_dir():
        return None
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target if target.is_file() else None


def list_reports(project_id: str) -> list[ReportEntry]:
    """List markdown reports under the project workspace (final first)."""
    try:
        root = project_workspace_dir(str(project_id), create=False).resolve()
    except ValueError:
        return []
    if not root.is_dir():
        return []

    seen: set[str] = set()
    out: list[ReportEntry] = []

    def _add(path: Path, kind: str) -> None:
        try:
            rel = path.resolve().relative_to(root).as_posix()
        except ValueError:
            return
        if rel in seen or not path.is_file():
            return
        seen.add(rel)
        try:
            st = path.stat()
            mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
            size = int(st.st_size)
        except OSError:
            mtime, size = None, 0
        suffix = path.suffix.lower()
        out.append(
            ReportEntry(
                name=path.name,
                relative_path=rel,
                size=size,
                modified_at=mtime,
                kind=kind,
                is_markdown=suffix in _MD_SUFFIXES,
            )
        )

    for pattern, kind in _SCAN_GLOBS:
        if "*" in pattern:
            for path in sorted(root.glob(pattern)):
                # report.md already claimed as final when present.
                if path.name.lower() == "report.md" and kind != "final":
                    continue
                _add(path, kind)
        else:
            _add(root / pattern, kind)

    order = {"final": 0, "plan": 1, "curated": 2}
    out.sort(key=lambda r: (order.get(r.kind, 9), r.relative_path))
    return out


def reports_payload(project_id: str) -> list[dict]:
    """JSON-serializable report hub rows for live UI polls."""
    rows: list[dict] = []
    for r in list_reports(project_id):
        rows.append(
            {
                "name": r.name,
                "relative_path": r.relative_path,
                "display_name": r.display_name,
                "size": r.size,
                "modified_at": r.modified_at.isoformat() if r.modified_at else None,
                "kind": r.kind,
                "is_markdown": r.is_markdown,
            }
        )
    return rows


# --- Project-scoped operator uploads (inputs/) ---

_INPUTS = "inputs"
_MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # soft cap for DoS; not a content validator
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._+-]+")


def inputs_dir(project_id: str, *, create: bool = True) -> Path:
    root = project_workspace_dir(str(project_id), create=create)
    path = (root / _INPUTS).resolve()
    path.relative_to(root.resolve())
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def sanitize_upload_name(name: str) -> str:
    base = Path(name or "").name.strip() or "upload.bin"
    cleaned = _SAFE_NAME.sub("_", base).strip("._") or "upload.bin"
    return cleaned[:180]


def resolve_input_path(project_id: str, name: str) -> Path | None:
    """Resolve a file under this project's inputs/ only (no path escape)."""
    try:
        root = inputs_dir(project_id, create=False).resolve()
    except (ValueError, OSError):
        return None
    if not root.is_dir():
        return None
    safe = sanitize_upload_name(Path(name or "").name)
    target = (root / safe).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target if target.is_file() else None


def list_project_inputs(project_id: str) -> list[dict]:
    """List files in ``<project>/inputs/`` (this project only)."""
    try:
        root = inputs_dir(project_id, create=True).resolve()
    except (ValueError, OSError):
        return []
    out: list[dict] = []
    for path in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not path.is_file():
            continue
        try:
            st = path.stat()
            mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
            size = int(st.st_size)
        except OSError:
            mtime, size = None, 0
        out.append(
            {
                "name": path.name,
                "relative_path": f"{_INPUTS}/{path.name}",
                "sandbox_path": f"/workspace/{_INPUTS}/{path.name}",
                "size": size,
                "modified_at": mtime.isoformat() if mtime else None,
            }
        )
    return out


def save_project_input(project_id: str, uploaded) -> dict:
    """Write an uploaded file into this project's inputs/ directory.

    Does not validate type or content — operator responsibility.
    """
    raw_name = getattr(uploaded, "name", "") or "upload.bin"
    name = sanitize_upload_name(raw_name)
    root = inputs_dir(project_id, create=True)
    dest = root / name
    # Avoid clobber: foo.bin → foo_1.bin …
    if dest.exists():
        stem = dest.stem
        suffix = dest.suffix
        n = 1
        while True:
            candidate = root / f"{stem}_{n}{suffix}"
            if not candidate.exists():
                dest = candidate
                name = dest.name
                break
            n += 1
            if n > 9999:
                raise OSError("Too many name collisions")

    size = 0
    with dest.open("wb") as out:
        for chunk in uploaded.chunks():
            size += len(chunk)
            if size > _MAX_UPLOAD_BYTES:
                out.close()
                dest.unlink(missing_ok=True)
                raise ValueError(
                    f"File exceeds {_MAX_UPLOAD_BYTES // (1024 * 1024)} MiB limit"
                )
            out.write(chunk)
    return {
        "name": name,
        "relative_path": f"{_INPUTS}/{name}",
        "sandbox_path": f"/workspace/{_INPUTS}/{name}",
        "size": size,
    }


def delete_project_input(project_id: str, name: str) -> bool:
    path = resolve_input_path(project_id, name)
    if path is None:
        return False
    path.unlink(missing_ok=True)
    return True

