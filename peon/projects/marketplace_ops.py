"""Marketplace sources: register, scan, and import discovered items.

Scanning never writes ``CatalogSkill``/``CatalogMcp``/``CatalogProposal``
directly — it only populates a ``CatalogMarketplaceSource.discovered``
summary. Importing one item always goes through the same code every other
install path already uses (``PlatformCatalog.upsert_skill_from_candidate``,
``install_mcp_source``, or the ``CatalogProposal`` review queue on the
Catalog page), so it inherits the same role/skill-registry invalidation and
validation those paths already have — nothing new to get right here.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_GITHUB_URL = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+/?$")


def _serialize(row) -> dict[str, Any]:
    return {
        "id": str(row.pk),
        "name": row.name,
        "source_type": row.source_type,
        "url": row.url,
        "ref": row.ref,
        "subpath": row.subpath,
        "status": row.status,
        "error": row.error,
        "discovered": row.discovered or {},
        "last_scanned_at": row.last_scanned_at.isoformat() if row.last_scanned_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


def list_sources() -> list[dict[str, Any]]:
    from peon.projects.models import CatalogMarketplaceSource

    return [_serialize(row) for row in CatalogMarketplaceSource.objects.all()]


def get_source(source_id: str):
    from peon.projects.models import CatalogMarketplaceSource

    return CatalogMarketplaceSource.objects.filter(pk=source_id).first()


def create_source(
    *,
    name: str,
    source_type: str,
    url: str = "",
    ref: str = "",
    subpath: str = "",
    archive_bytes: bytes | None = None,
) -> dict[str, Any]:
    from peon.projects.models import CatalogMarketplaceSource

    stype = str(source_type or "").strip().lower()
    if stype not in {"github", "zip"}:
        raise ValueError("source_type must be github or zip")
    if stype == "github":
        clean_url = str(url or "").strip()
        if not _GITHUB_URL.match(clean_url):
            raise ValueError("url must be a plain https://github.com/<owner>/<repo> address")
    elif not archive_bytes:
        raise ValueError("a zip file is required")
    row = CatalogMarketplaceSource.objects.create(
        name=str(name or "").strip() or "Untitled source",
        source_type=stype,
        url=str(url or "").strip() if stype == "github" else "",
        ref=str(ref or "").strip(),
        subpath=str(subpath or "").strip(),
    )
    if stype == "zip":
        from orchestrator.marketplace.fetch import source_dir

        dest = source_dir(str(row.pk))
        dest.mkdir(parents=True, exist_ok=True)
        archive_path = dest / "source.zip"
        archive_path.write_bytes(archive_bytes)
        row.archive_path = str(archive_path)
        row.save(update_fields=["archive_path"])
    return _serialize(row)


def delete_source(source_id: str) -> None:
    import shutil

    from peon.projects.models import CatalogMarketplaceSource

    row = CatalogMarketplaceSource.objects.filter(pk=source_id).first()
    if row is None:
        raise ValueError("unknown source")
    row.delete()
    from orchestrator.marketplace.fetch import source_dir

    shutil.rmtree(source_dir(source_id), ignore_errors=True)


def _fetch(row) -> Any:
    from orchestrator.marketplace.fetch import fetch_github, fetch_zip_archive

    if row.source_type == "github":
        return fetch_github(str(row.pk), row.url, ref=row.ref)
    return fetch_zip_archive(str(row.pk), row.archive_path)


def scan_source(source_id: str) -> dict[str, Any]:
    """Fetch (or re-fetch) the source and record what it contains. No imports."""
    from django.utils import timezone as dj_tz

    from orchestrator.marketplace.fetch import scan_root
    from orchestrator.marketplace.scan import scan_all

    row = get_source(source_id)
    if row is None:
        raise ValueError("unknown source")
    row.status = "scanning"
    row.error = ""
    row.save(update_fields=["status", "error"])
    try:
        fetched = _fetch(row)
        target = scan_root(fetched, row.subpath)
        row.discovered = scan_all(target)
        row.status = "scanned"
        row.last_scanned_at = dj_tz.now()
        row.save(update_fields=["discovered", "status", "last_scanned_at"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("marketplace scan failed for %s: %s", source_id, exc)
        row.status = "error"
        row.error = str(exc)[:1000]
        row.save(update_fields=["status", "error"])
        raise
    return _serialize(row)


def import_skill(source_id: str, *, path: str) -> dict[str, Any]:
    """Import one discovered skill (by its path within the source) into the catalog."""
    from orchestrator.marketplace.fetch import scan_root
    from orchestrator.skills.providers.base import CLAUDE_PROVIDER
    from orchestrator.skills.registry_scan import discover_skill_candidates
    from peon.projects.platform_catalog import PlatformCatalog

    row = get_source(source_id)
    if row is None:
        raise ValueError("unknown source")
    fetched = _fetch(row)
    target = scan_root(fetched, row.subpath)
    want = (target / str(path or "")).resolve()
    candidates = [
        c
        for c in discover_skill_candidates(target, source="marketplace", skip_helpers=True)
        if c.root.resolve() == want
    ]
    if not candidates:
        raise ValueError("skill not found at that path — rescan the source")
    rec = PlatformCatalog.shared().upsert_skill_from_candidate(
        candidates[0], default_provider=CLAUDE_PROVIDER
    )
    if rec is None:
        raise ValueError("import produced nothing")
    return {"ok": True, "kind": "skill", "id": rec.skill_id}


def import_mcp_pointer(source_id: str, *, repo_url: str, ref: str = "") -> dict[str, Any]:
    """Install the MCP server a pointer manifest names, via the existing git installer."""
    from orchestrator.mcp.install import install_mcp_source

    row = get_source(source_id)
    if row is None:
        raise ValueError("unknown source")
    ids = install_mcp_source("git", repo_url, revision=ref)
    return {"ok": True, "kind": "mcp", "ids": ids}


def import_agent(source_id: str, *, name: str, description: str, instructions: str) -> dict[str, Any]:
    """Stage a discovered agent config as a draft role proposal for review.

    Reuses the exact ``CatalogProposal`` review queue (Approve/Reject on the
    Catalog page) rather than a second, marketplace-only review mechanism.
    """
    from peon.projects.models import CatalogProposal

    row = get_source(source_id)
    if row is None:
        raise ValueError("unknown source")
    role_id = str(name or "").strip().lower()
    if not role_id:
        raise ValueError("agent name is required")
    existing = CatalogProposal.objects.filter(
        status="pending", kind="role", action="create", payload__role__role_id=role_id
    ).first()
    if existing is not None:
        return {"ok": True, "kind": "role_proposal", "id": str(existing.pk), "already_pending": True}
    backstory = str(instructions or "").strip() or str(description or "").strip()
    proposal = CatalogProposal.objects.create(
        kind="role",
        action="create",
        target_id="",
        title=f"New role from marketplace: {role_id}",
        rationale=f"Discovered in marketplace source {row.name!r} ({row.url or row.source_type}).",
        payload={
            "role": {
                "role_id": role_id,
                "kind": "technical",
                "aliases": [],
                "goal": str(description or "").strip() or f"Imported from {row.name}",
                "backstory": backstory,
            }
        },
    )
    return {"ok": True, "kind": "role_proposal", "id": str(proposal.pk)}
