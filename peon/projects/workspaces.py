"""Per-project filesystem workspace roots (isolated under PROJECT_WORKSPACES_DIR)."""

from __future__ import annotations

import logging
import re
import shutil
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings

from orchestrator.utils.paths import (
    ensure_workspace_layout,
    safe_join,
    safe_workspace_key,
)

logger = logging.getLogger(__name__)


def workspaces_root() -> Path:
    root = Path(
        getattr(settings, "PROJECT_WORKSPACES_DIR", "workspaces")
    ).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def platform_workspace_dir() -> Path:
    root = Path(
        getattr(
            settings,
            "PLATFORM_WORKSPACE_DIR",
            workspaces_root() / "platform",
        )
    ).resolve()
    root.relative_to(workspaces_root())
    root.mkdir(parents=True, exist_ok=True)
    return root


def project_workspace_dir(project_id: str, *, create: bool = True) -> Path:
    """Return ``PROJECT_WORKSPACES_DIR / <project_id>`` (only that project)."""
    root = workspaces_root()
    try:
        key = str(uuid.UUID(str(project_id)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"invalid project id {project_id!r}") from exc
    path = (root / key).resolve()
    path.relative_to(root)  # raises if escaped
    if create:
        ensure_workspace_layout(path)
    return path


def resolve_job_workspace(job) -> Path:
    """Project jobs use their project folder; orphan jobs stay under platform/jobs."""
    pid = getattr(job, "project_id", None)
    if pid:
        path = project_workspace_dir(str(pid))
        wid = safe_workspace_key(str(pid))
        if getattr(job, "workspace_id", None) != wid:
            job.workspace_id = wid
            try:
                job.save(update_fields=["workspace_id", "updated_at"])
            except Exception as exc:
                logger.warning("saving job workspace id failed: %s", exc, exc_info=True)
        return path
    wid = safe_workspace_key(
        (getattr(job, "workspace_id", None) or "").strip() or f"job-{job.id}"
    )
    if getattr(job, "workspace_id", None) != wid:
        job.workspace_id = wid
        try:
            job.save(update_fields=["workspace_id", "updated_at"])
        except Exception as exc:
            logger.warning("saving job workspace id failed: %s", exc, exc_info=True)
    platform = platform_workspace_dir()
    path = (platform / "jobs" / wid).resolve()
    path.relative_to(platform)
    return ensure_workspace_layout(path)


_DELETING_PREFIX = ".deleting-"


def _rmtree_force(path: Path) -> None:
    """``shutil.rmtree`` with chmod retry for read-only entries."""

    def _onexc(func, p, exc):  # noqa: ANN001
        target = Path(p)
        try:
            if target.is_dir() and not target.is_symlink():
                target.chmod(0o700)
            elif target.exists() or target.is_symlink():
                target.chmod(0o600)
            func(p)
        except OSError:
            # The retry also failed — re-raise the original error on purpose,
            # not this one (there is nothing more specific to add to it).
            raise exc from None

    shutil.rmtree(path, onexc=_onexc)


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    else:
        _rmtree_force(path)


def workspace_entry_project_id(name: str) -> str:
    """UUID from a workspace dir name, including ``.deleting-<uuid>`` trash."""
    raw = name.removeprefix(_DELETING_PREFIX) if name.startswith(_DELETING_PREFIX) else name
    try:
        return str(uuid.UUID(raw))
    except (ValueError, AttributeError, TypeError):
        return ""


def release_project_handles(project_id: str) -> None:
    """Close project SQLite alias and local PTY sessions before disk delete."""
    from peon.projects.db import drop_project_database

    drop_project_database(project_id)
    try:
        from peon.projects.terminal_session import get_registry

        get_registry().close_project(project_id)
    except Exception as exc:
        logger.warning("closing project terminals failed: %s", exc, exc_info=True)


def remove_project_workspace(project_id: str) -> dict:
    """Delete one project's complete workspace tree.

    When files resist deletion (e.g. root-owned by a sandbox container) the tree
    is renamed to ``.deleting-<id>`` so the id is free at once and the leftovers
    are retried here and by ``prune_orphan_workspaces``.
    """
    try:
        path = project_workspace_dir(project_id, create=False)
    except ValueError:
        return {"action": "skipped", "reason": "unsafe id"}
    if not path.exists():
        return {"action": "missing", "path": str(path)}
    try:
        _remove_path(path)
        return {"action": "removed" if not path.exists() else "error", "path": str(path)}
    except OSError as exc:
        error = str(exc)
    trash = path.parent / f"{_DELETING_PREFIX}{path.name}"
    try:
        if trash.exists():
            _remove_path(trash)
        path.rename(trash)
    except OSError as rename_exc:
        return {"action": "error", "path": str(path), "error": f"{error}; rename failed: {rename_exc}"}
    try:
        _remove_path(trash)
    except OSError:
        pass
    return {
        "action": "deferred" if trash.exists() else "removed",
        "path": str(path),
        "trash": str(trash) if trash.exists() else "",
        "error": error,
    }


def prune_orphan_workspaces(
    active_project_ids: Iterable[str],
    *,
    persist: bool = False,
) -> dict[str, list[str]]:
    """Find or remove workspace entries not owned by a current Project."""
    active = {
        pid
        for value in active_project_ids
        if (pid := workspace_entry_project_id(str(value)))
    }
    root = workspaces_root()
    orphans: list[str] = []
    removed: list[str] = []
    errors: list[str] = []
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if path.name == "platform":
            continue
        name = path.name
        deferred = name.startswith(_DELETING_PREFIX)
        identity = workspace_entry_project_id(name)
        if identity and identity in active and not deferred:
            continue
        orphans.append(name)
        if not persist:
            continue
        try:
            _remove_path(path)
            removed.append(name)
        except OSError as exc:
            errors.append(f"{name}: {exc}")
    return {"orphans": orphans, "removed": removed, "errors": errors}


# --- Report discovery ---

_MD_SUFFIXES = {".md", ".markdown"}
_SCAN_GLOBS = (
    ("findings/report.md", "final"),
    ("findings/*.md", "curated"),
    ("plans/latest.md", "plan"),
)


def is_markdown_path(path: Path | str) -> bool:
    return Path(path).suffix.lower() in _MD_SUFFIXES


def render_markdown_html(text: str) -> str:
    """Render Markdown to sanitized HTML; empty string if the markdown package is missing.

    Reports are agent-written and may echo target content, so raw HTML is
    stripped down to a safe allowlist before the template marks it ``|safe``.
    """
    try:
        import markdown as md
    except ImportError:
        return ""
    import nh3

    html = md.markdown(
        text,
        extensions=["fenced_code", "tables", "nl2br", "sane_lists"],
        output_format="html",
    )
    return nh3.clean(html, link_rel="noopener noreferrer nofollow")


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

    try:
        root = project_workspace_dir(str(project_id), create=False).resolve()
    except ValueError:
        return None
    if not root.is_dir():
        return None
    target = safe_join(root, relative_path)
    return target if target is not None and target.is_file() else None


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
            mtime = datetime.fromtimestamp(st.st_mtime, tz=UTC)
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
    target = safe_join(root, sanitize_upload_name(Path(name or "").name))
    return target if target is not None and target.is_file() else None


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
            mtime = datetime.fromtimestamp(st.st_mtime, tz=UTC)
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


# --- Prior-agent evidence discovery (tool-agnostic) ---

_ARTIFACT_SKIP_NAMES = frozenset({"findings_queue.jsonl", "analysis_context.json"})
_ARTIFACT_SKIP_SUFFIXES = (".ingested", ".pyc")


def iter_workspace_artifacts(
    workspace: Path, *, limit: int = 80
) -> list[tuple[str, int]]:
    """Relative paths + sizes for evidence files under ``workspace/`` and ``findings/``.

    Tool-agnostic: any prior agent output lands here; callers decide how to parse.
    """
    root = Path(workspace).resolve()
    out: list[tuple[str, int]] = []
    seen: set[str] = set()
    for sub in ("workspace", "findings"):
        base = root / sub
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if path.name.startswith(".") or path.name in _ARTIFACT_SKIP_NAMES:
                continue
            if path.name.endswith(_ARTIFACT_SKIP_SUFFIXES):
                continue
            if path.name.lower() == "report.md" and sub == "findings":
                continue
            try:
                rel = path.resolve().relative_to(root).as_posix()
                size = int(path.stat().st_size)
            except (ValueError, OSError):
                continue
            if rel in seen:
                continue
            seen.add(rel)
            out.append((rel, size))
            if len(out) >= limit:
                return out
    return out


def format_workspace_artifact_index(workspace: Path, *, limit: int = 80) -> str:
    """Markdown bullet list of evidence files for job briefs / tools."""
    rows = iter_workspace_artifacts(workspace, limit=limit)
    if not rows:
        return ""
    lines = [f"- `{rel}` ({size} bytes)" for rel, size in rows]
    return "\n".join(lines)


def _is_manager_role(role_id: str) -> bool:
    """Managers dispatch; they do not need the prior-artifact index."""
    rid = (role_id or "").strip()
    if not rid:
        return False
    try:
        from orchestrator.crew.roles.registry import RoleRegistry

        role = RoleRegistry.shared().get(rid)
    except Exception:  # noqa: BLE001
        return False
    return bool(role is not None and role.is_manager)


def brief_artifact_section(project_id: str, *, role_id: str = "", limit: int = 80) -> str:
    """Artifact index block for objective briefs (skipped for manager roles)."""
    if _is_manager_role(role_id):
        return ""
    try:
        ws = project_workspace_dir(str(project_id), create=False)
        index = format_workspace_artifact_index(ws, limit=limit)
    except Exception:
        return ""
    if not index:
        return ""
    return (
        "## Workspace artifacts from prior agents\n"
        "Read these with list_workspace_artifacts / "
        "read_workspace_artifact (do not re-scan to rediscover them):\n"
        f"{index}"
    )

