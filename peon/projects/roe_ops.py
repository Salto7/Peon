"""Rules of Engagement operations for project targets."""

from __future__ import annotations

import logging

from typing import Any, Iterable

from peon.projects.models import Project, RulesOfEngagement
from peon.projects.target_discovery import SubjectCanonicalizer
from peon.projects.target_shapes import (
    as_asset_name,
    coerce_targets,
    extract_targets,
    is_subject_type,
)
from peon.projects.asset_graph import AssetGraphEngine

logger = logging.getLogger(__name__)

def roe_block_reason(role_ids: Iterable[str] | None, scope: Iterable[Any] | None) -> str | None:
    """Fail-closed when technical probe roles have no in-scope *values*."""
    names = [str(n).strip() for n in (role_ids or []) if str(n).strip()]
    if coerce_targets(scope):
        return None
    active = SubjectCanonicalizer.active_probe_role_ids()
    needing = [n for n in names if n in active]
    if not needing:
        return None
    return (
        "Rules of Engagement fail-closed: in_scope has no authorized targets for "
        f"{', '.join(needing)} (add targets or deduce from brief)"
    )


def get_or_create_roe(project: Project) -> RulesOfEngagement:
    """Load/create RoE on the project SQLite file (never the platform default DB)."""
    from peon.projects.db import ensure_project_database, using_project

    alias = ensure_project_database(project.pk)
    with using_project(project.pk):
        roe, _ = RulesOfEngagement.objects.using(alias).get_or_create(project=project)
    return roe


def provision_project_roe(
    project: Project,
    *,
    texts: Iterable[str] | None = None,
    exclusions: list | None = None,
    authorization: str = "",
) -> tuple[RulesOfEngagement, list[str]]:
    """
    Ensure RoE exists. If in_scope is empty, deduce assets from texts /
    title+summary into in_scope. Type labels are soft producer hints.
    """
    from peon.projects.db import ensure_project_database, using_project

    roe = get_or_create_roe(project)
    alias = ensure_project_database(project.pk)
    deduced: list[str] = []
    with using_project(project.pk):
        # Re-validate stored rows against the asset-name shape contract.
        title_label = (project.title or "").strip().lower()
        cleaned_scope: list[dict[str, str]] = []
        for row in coerce_targets(roe.in_scope):
            typ = str(row.get("type") or "")
            val = str(row.get("value") or "").strip()
            if not is_subject_type(typ):
                continue
            if typ == "asset":
                if as_asset_name(val, explicit=True) is None:
                    continue
                # Title is a label unless it itself has proper-name shape.
                if title_label and val.lower() == title_label and as_asset_name(val) is None:
                    continue
            cleaned_scope.append(row)
        scope = cleaned_scope
        if scope != list(roe.in_scope or []):
            roe.in_scope = scope
            roe.save(using=alias, update_fields=["in_scope", "updated_at"])
        if not scope:
            # Prefer operator brief — never job objective prose or title labels.
            parts = [project.summary or ""]
            parts.extend(str(t or "") for t in (texts or []) if str(t or "").strip())
            assets = extract_targets(*parts)
            if not assets:
                # Same brief corpus as extract_targets (summary + provision texts).
                blob = "\n".join(p.strip() for p in parts if (p or "").strip())
                if len(blob) >= 2:
                    assets = coerce_targets(SubjectCanonicalizer.suggest_assets(blob))
            if not assets:
                assets = extract_targets(project.title or "")
            if assets:
                roe.in_scope = assets
                deduced = [t["value"] for t in assets]
                if authorization and not roe.authorization_note:
                    roe.authorization_note = authorization
                elif not roe.authorization_note:
                    roe.authorization_note = (
                        "Auto-deduced from brief (operator did not supply Rules of Engagement)."
                    )
                if not coerce_targets(getattr(roe, "seed", None)):
                    seed_bits = [
                        s.strip()
                        for s in (project.title, project.summary)
                        if (s or "").strip()
                    ]
                    if seed_bits:
                        roe.seed = coerce_targets(
                            [{"type": "other", "value": " | ".join(seed_bits)[:500]}]
                        )
                roe.save(using=alias)
                scope = assets
        elif scope != list(roe.in_scope or []):
            roe.in_scope = scope
            roe.save(using=alias, update_fields=["in_scope", "updated_at"])

        if exclusions is not None:
            roe.exclusions = coerce_targets(exclusions)
            roe.save(using=alias, update_fields=["exclusions", "updated_at"])
        if authorization and not roe.authorization_note:
            roe.authorization_note = authorization
            roe.save(using=alias, update_fields=["authorization_note", "updated_at"])
    return roe, deduced


def _escalate_scope_change(roe: RulesOfEngagement, added: list[dict[str, str]]) -> None:
    """Wake the resident governance PM so it can re-govern after a scope change."""
    if not added:
        return
    project = getattr(roe, "project", None)
    if project is None:
        return
    try:
        from peon.projects import a2a

        vals = ", ".join(t.get("value", "") for t in added if t.get("value"))[:300]
        a2a.notify_governor(
            project,
            text=f"Scope changed: {len(added)} target(s) now in RoE in_scope: {vals}",
            escalate=True,
        )
    except Exception:
        logger.debug("governor scope-escalate failed", exc_info=True)


def promote_candidates(
    roe: RulesOfEngagement,
    *,
    indices: list[int] | None = None,
    all_candidates: bool = False,
) -> int:
    """Move selected (or all) candidates into in_scope. Returns count promoted."""
    candidates = coerce_targets(roe.candidates)
    if not candidates:
        return 0
    if all_candidates:
        chosen = candidates
        remaining: list[dict[str, str]] = []
    else:
        wanted = set(indices or [])
        chosen = [c for i, c in enumerate(candidates) if i in wanted]
        remaining = [c for i, c in enumerate(candidates) if i not in wanted]
    if not chosen:
        return 0
    scope = coerce_targets(roe.in_scope)
    scope.extend(chosen)
    from peon.projects.db import project_alias_or_default

    roe.in_scope = coerce_targets(scope)
    roe.candidates = remaining
    roe.save(
        using=project_alias_or_default(roe.project_id),
        update_fields=["in_scope", "candidates", "updated_at"],
    )
    _escalate_scope_change(roe, chosen)
    return len(chosen)


def add_candidates(roe: RulesOfEngagement, raw: Iterable) -> int:
    """Append discovered assets as candidates (not yet authorized)."""
    incoming = coerce_targets(raw)
    if not incoming:
        return 0
    existing = coerce_targets(roe.candidates)
    before = len(existing)
    existing.extend(incoming)
    roe.candidates = coerce_targets(existing)
    roe.save(update_fields=["candidates", "updated_at"])
    try:
        project = getattr(roe, "project", None)
        if project is not None:
            AssetGraphEngine(project).ingest(
                incoming,
                bucket="candidate",
                source="rules_of_engagement",
            )
    except Exception as exc:
        logger.warning("asset graph ingest failed: %s", exc, exc_info=True)
    return len(roe.candidates) - before

def authorize_operator_scope(roe: RulesOfEngagement, text: str) -> list[dict[str, str]]:
    """Merge operator-named assets into RoE in_scope (chat/replan = authorization)."""
    blob = text or ""
    incoming = extract_targets(blob)
    if not incoming and len(blob.strip()) >= 8:
        incoming = SubjectCanonicalizer.suggest_assets(blob)
    if not incoming:
        return []
    known = {
        t["value"].lower()
        for t in coerce_targets(roe.in_scope) + coerce_targets(roe.exclusions)
        if t.get("value")
    }
    novel = [t for t in incoming if t.get("value", "").lower() not in known]
    if not novel:
        return []
    scope = coerce_targets(roe.in_scope)
    scope.extend(novel)
    roe.in_scope = coerce_targets(scope)
    cands = [
        c
        for c in coerce_targets(roe.candidates)
        if c.get("value", "").lower() not in {n["value"].lower() for n in novel}
    ]
    roe.candidates = cands
    roe.save(update_fields=["in_scope", "candidates", "updated_at"])
    _escalate_scope_change(roe, novel)
    return novel


def roe_add_path(project: Project, sandbox_path: str) -> None:
    """Authorize a project-local input path in RoE seed + in_scope."""
    from peon.projects.db import ensure_project_database, using_project

    alias = ensure_project_database(project.pk)
    path = (sandbox_path or "").strip()
    if not path:
        return
    roe = get_or_create_roe(project)
    with using_project(project.pk):
        seed = coerce_targets(roe.seed)
        scope = coerce_targets(roe.in_scope)
        row = {"type": "file", "value": path}
        if not any(t.get("value") == path for t in seed):
            seed.append(row)
        if not any(t.get("value") == path for t in scope):
            scope.append(row)
        roe.seed = seed
        roe.in_scope = scope
        roe.save(using=alias, update_fields=["seed", "in_scope", "updated_at"])


def roe_remove_path(project: Project, sandbox_path: str) -> None:
    """Drop a path value from RoE seed + in_scope (if present)."""
    roe = getattr(project, "roe", None)
    if roe is None:
        return
    path = (sandbox_path or "").strip()
    seed = [t for t in coerce_targets(roe.seed) if t.get("value") != path]
    scope = [t for t in coerce_targets(roe.in_scope) if t.get("value") != path]
    roe.seed = seed
    roe.in_scope = scope
    roe.save(update_fields=["seed", "in_scope", "updated_at"])
