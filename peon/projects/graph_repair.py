"""Canonicalize finding subjects and rebuild attack-surface projections."""

from __future__ import annotations

from typing import Any

from peon.projects.asset_graph import AssetGraphEngine
from peon.projects.models import Finding, Project, RulesOfEngagement
from peon.projects.target_discovery import SubjectCanonicalizer
from peon.projects.target_shapes import canonical_asset_key, coerce_targets, detect_shape


def _provenance_paths(project: Project) -> set[str]:
    paths: set[str] = set()
    for path in project.findings.values_list("evidence_path", flat=True):
        cleaned = str(path or "").strip().lower()
        if cleaned:
            paths.add(cleaned)
    return paths


def _subject_values(project: Project, *, persist: bool) -> set[str]:
    values: set[str] = set()
    for finding in project.findings.all().iterator():
        subjects = SubjectCanonicalizer.from_finding(
            {
                "host": finding.host,
                "port": finding.port,
                "protocol": finding.protocol,
                "asset_type": finding.asset_type,
                "metadata": finding.metadata
                if isinstance(finding.metadata, dict)
                else {},
                "subjects": finding.subjects,
            }
        )
        if persist and subjects != list(finding.subjects or []):
            from peon.projects.db import qs_using

            qs_using(Finding, project.id).filter(pk=finding.pk).update(
                subjects=subjects
            )
        for row in subjects:
            val = str(row.get("value") or "").strip().lower()
            if val:
                values.add(val)
    return values


def _graph_health(payload: dict[str, Any]) -> dict[str, int]:
    """Return generic post-rebuild invariants for operational verification."""
    nodes = list(payload.get("nodes") or [])
    edges = list(payload.get("edges") or [])
    node_ids = {str(node.get("id") or "") for node in nodes}
    identities: set[str] = set()
    duplicate_identities = 0
    orphan_services = 0
    for node in nodes:
        value = str(node.get("value") or "")
        identity = canonical_asset_key(str(node.get("type") or ""), value)
        if identity in identities:
            duplicate_identities += 1
        identities.add(identity)
        if detect_shape(value) == "service" and not node.get("parent_id"):
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
            if key in provenance and key not in subjects:
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
        if (
            typ in {"file", "path"}
            and key in provenance
            and key not in subjects
        ):
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

    engine = AssetGraphEngine(project)
    payload = engine.engagement(
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
