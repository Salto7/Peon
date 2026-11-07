"""Canonicalize finding subjects and rebuild attack-surface projections."""

from __future__ import annotations

from typing import Any

from peon.projects.asset_graph import AssetGraphEngine, finding_subject_row
from peon.projects.asset_model import is_bare_port_value
from peon.projects.models import Finding, Project, RulesOfEngagement
from peon.projects.target_discovery import subjects_from_finding
from peon.projects.targets import canonical_asset_key, coerce_targets, sanitize_label


def _provenance_paths(project: Project) -> set[str]:
    return {
        cleaned
        for path in project.findings.values_list("evidence_path", flat=True)
        if (cleaned := str(path or "").strip().lower())
    }


def _subject_values(project: Project, *, persist: bool) -> set[str]:
    values: set[str] = set()
    for finding in project.findings.all().iterator():
        subjects = subjects_from_finding(finding_subject_row(finding))
        if persist and subjects != list(finding.subjects or []):
            from peon.projects.db import qs_using

            qs_using(Finding, project.id).filter(pk=finding.pk).update(subjects=subjects)
        for row in subjects:
            val = str(row.get("value") or "").strip().lower()
            if val:
                values.add(val)
    return values


def _graph_health(payload: dict[str, Any]) -> dict[str, int]:
    nodes = list(payload.get("nodes") or [])
    edges = list(payload.get("edges") or [])
    node_ids = {str(n.get("id") or "") for n in nodes}
    port_targets = {
        str(e.get("target") or "")
        for e in edges
        if sanitize_label(str(e.get("rel") or "")) == "port"
    }
    identities: set[str] = set()
    duplicate_identities = 0
    orphan_services = 0
    for node in nodes:
        value = str(node.get("value") or "")
        identity = canonical_asset_key(str(node.get("type") or ""), value)
        if identity in identities:
            duplicate_identities += 1
        identities.add(identity)
        nid = str(node.get("id") or "")
        if sanitize_label(str(node.get("type") or "")) == "service" and nid not in port_targets:
            orphan_services += 1
    dangling_edges = sum(
        1
        for edge in edges
        if str(edge.get("source") or "") not in node_ids
        or str(edge.get("target") or "") not in node_ids
    )
    return {
        "duplicate_identities": duplicate_identities,
        "orphan_services": orphan_services,
        "dangling_edges": dangling_edges,
    }


def repair_project_graph(project: Project, *, persist: bool = True) -> dict[str, Any]:
    """Normalize source contracts, remove legacy pollution, and rebuild."""
    provenance = _provenance_paths(project)
    subjects = _subject_values(project, persist=persist)
    removed_candidates: list[str] = []
    removed_assets: list[str] = []

    roe = getattr(project, "roe", None)
    if isinstance(roe, RulesOfEngagement):
        kept = []
        for raw in coerce_targets(roe.candidates):
            value = str(raw.get("value") or "").strip()
            key = value.lower()
            stale = key in provenance and key not in subjects
            if stale or is_bare_port_value(str(raw.get("type") or ""), value):
                removed_candidates.append(value)
                continue
            kept.append(raw)
        if removed_candidates:
            roe.candidates = kept
            if persist:
                roe.save(update_fields=["candidates", "updated_at"])

    graph = AssetGraphEngine(project).graph
    kept_assets = []
    drop_ids: set[str] = set()
    for asset in list(graph.assets or []):
        value = str(asset.get("value") or "").strip()
        key = value.lower()
        typ = str(asset.get("type") or "").strip().lower()
        stale = typ in {"file", "path"} and key in provenance and key not in subjects
        if stale or is_bare_port_value(typ, value):
            drop_ids.add(str(asset.get("id") or ""))
            removed_assets.append(value)
            continue
        kept_assets.append(asset)
    if drop_ids:
        graph.assets = kept_assets
        graph.relations = [
            e
            for e in (graph.relations or [])
            if str(e.get("source") or "") not in drop_ids
            and str(e.get("target") or "") not in drop_ids
        ]
        if persist:
            graph.save()

    payload = AssetGraphEngine(project).engagement(
        findings=list(project.findings.all()),
        persist=persist,
        rebuild=True,
    )
    return {
        "project_id": str(project.id),
        "removed_candidates": removed_candidates,
        "removed_assets": removed_assets,
        "node_count": len(payload.get("nodes") or []),
        "edge_count": len(payload.get("edges") or []),
        **_graph_health(payload),
    }


def repair_all_projects(*, persist: bool = True) -> list[dict[str, Any]]:
    return [
        repair_project_graph(project, persist=persist)
        for project in Project.objects.all().iterator()
    ]
