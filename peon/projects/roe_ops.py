"""Rules of Engagement operations for project targets."""

from __future__ import annotations

from typing import Any, Iterable

from peon.projects.models import Project, RulesOfEngagement
from peon.projects.target_discovery import _active_probe_role_ids, _llm_suggest_assets
from peon.projects.target_shapes import (
    coerce_targets,
    extract_targets,
)
from peon.projects.asset_graph import ingest_assets

def roe_block_reason(role_ids: Iterable[str] | None, scope: Iterable[Any] | None) -> str | None:
    """Fail-closed when requires_roe roles have no in-scope *values*."""
    names = [str(n).strip() for n in (role_ids or []) if str(n).strip()]
    if coerce_targets(scope):
        return None
    active = _active_probe_role_ids()
    needing = [n for n in names if n in active]
    if not needing:
        return None
    return (
        "Rules of Engagement fail-closed: in_scope has no authorized targets for "
        f"{', '.join(needing)} (add targets or deduce from brief)"
    )


# Back-compat aliases (call sites that iterate the frozenset get a live snapshot).
def __getattr__(name: str):
    if name == "ACTIVE_NETWORK_ROLES":
        return _active_probe_role_ids()
    raise AttributeError(name)

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
    roe, _ = RulesOfEngagement.objects.get_or_create(project=project)
    deduced: list[str] = []
    scope = coerce_targets(roe.in_scope)
    if not scope:
        parts = list(texts or [])
        parts.extend([project.title or "", project.summary or ""])
        assets = extract_targets(*parts)
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
            roe.save()
            scope = assets
    elif scope != list(roe.in_scope or []):
        roe.in_scope = scope
        roe.save(update_fields=["in_scope", "updated_at"])

    if exclusions is not None:
        roe.exclusions = coerce_targets(exclusions)
        roe.save(update_fields=["exclusions", "updated_at"])
    if authorization and not roe.authorization_note:
        roe.authorization_note = authorization
        roe.save(update_fields=["authorization_note", "updated_at"])
    try:
        project.roe = roe
    except Exception:
        pass
    return roe, deduced


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
    roe.in_scope = coerce_targets(scope)
    roe.candidates = remaining
    roe.save(update_fields=["in_scope", "candidates", "updated_at"])
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
            ingest_assets(project, incoming, bucket="candidate", source="rules_of_engagement")
    except Exception:
        pass
    return len(roe.candidates) - before

def authorize_operator_scope(roe: RulesOfEngagement, text: str) -> list[dict[str, str]]:
    """Merge operator-named assets into RoE in_scope (chat/replan = authorization)."""
    blob = text or ""
    incoming = extract_targets(blob)
    if not incoming and len(blob.strip()) >= 8:
        incoming = _llm_suggest_assets(blob)
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
    return novel


def roe_add_path(project: Project, sandbox_path: str) -> None:
    """Authorize a project-local input path in RoE seed + in_scope."""
    roe, _ = RulesOfEngagement.objects.get_or_create(project=project)
    path = (sandbox_path or "").strip()
    if not path:
        return
    seed = coerce_targets(roe.seed)
    scope = coerce_targets(roe.in_scope)
    row = {"type": "file", "value": path}
    if not any(t.get("value") == path for t in seed):
        seed.append(row)
    if not any(t.get("value") == path for t in scope):
        scope.append(row)
    roe.seed = seed
    roe.in_scope = scope
    roe.save(update_fields=["seed", "in_scope", "updated_at"])


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
