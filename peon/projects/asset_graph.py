"""OAM-inspired engagement asset graph store (open types & relations).

Producers (RoE, findings, roles, operator, tools) upsert assets and typed
edges. Types and ``rel`` labels are free-form slugs — the UI must render
unknown kinds without a Python/JS allowlist.

RoE buckets remain the authorization overlay; this graph is the engagement
surface SoT for visualization and future enrichment.
"""

from __future__ import annotations

import hashlib
import ipaddress
from typing import Any, Iterable

from peon.projects.models import AssetGraph, Project
from peon.projects.target_discovery import SubjectCanonicalizer
from peon.projects.target_shapes import (
    accept_asset,
    canonical_asset_key,
    coerce_targets,
    detect_shape,
    parse_service,
    sanitize_label,
    url_host,
)


def _stable_id(*parts: str) -> str:
    raw = "|".join((p or "").strip().lower() for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def asset_key(typ: str, value: str) -> str:
    """Canonical identity key independent of producer label aliases."""
    return canonical_asset_key(typ, value)


def _prefer_type(previous: str, new: str, value: str) -> str:
    """Keep the most precise display type while identity stays canonical."""
    shape = detect_shape(value)
    if shape:
        return shape
    if previous in {"", "other", "host"} and new not in {"", "other"}:
        return new
    return previous or new or shape or "other"


def _get_or_create_graph(project: Project) -> AssetGraph:
    import time

    from django.db import OperationalError

    from peon.projects.db import ensure_project_database, using_project

    last: Exception | None = None
    for attempt in range(6):
        try:
            alias = ensure_project_database(project.pk)
            with using_project(project.pk):
                graph, _ = AssetGraph.objects.using(alias).get_or_create(project=project)
            return graph
        except OperationalError as exc:
            last = exc
            if "locked" not in str(exc).lower():
                raise
            time.sleep(0.05 * (2**attempt))
    assert last is not None
    raise last


def _upsert_asset(
    graph: AssetGraph,
    *,
    typ: str,
    value: str,
    bucket: str = "discovered",
    source: str = "",
    props: dict | None = None,
) -> dict[str, Any] | None:
    """Insert or merge one asset. Returns the stored row (or None if empty)."""
    accepted = accept_asset(value, typ)
    if accepted is None:
        cleaned = (value or "").strip()
        if not cleaned:
            return None
        accepted = {
            "type": sanitize_label(typ, default="other") or "other",
            "value": cleaned[:1024],
        }
    # Intrinsic value shapes are authoritative inside the graph. Producer
    # labels remain useful outside it, but cannot make one IP appear as both
    # ``host`` and ``ip`` nodes.
    typ = detect_shape(accepted["value"]) or accepted["type"]
    value = accepted["value"]
    key = asset_key(typ, value)
    nid = _stable_id("a", key)
    assets = list(graph.assets or [])
    by_key = {str(a.get("key") or ""): i for i, a in enumerate(assets)}
    by_id = {str(a.get("id") or ""): i for i, a in enumerate(assets)}
    idx = by_key.get(key)
    if idx is None:
        idx = by_id.get(nid)
    row = {
        "id": nid if idx is None else assets[idx].get("id") or nid,
        "type": typ,
        "value": value,
        "key": key,
        "bucket": sanitize_label(bucket, default="discovered") or "discovered",
        "source": (source or "")[:120],
        "props": dict(props or {}),
    }
    if idx is None:
        assets.append(row)
    else:
        prev = assets[idx]
        # Prefer non-other type; keep richest bucket (in_scope > seed > candidate …)
        prev["type"] = _prefer_type(
            str(prev.get("type") or ""), row["type"], value
        )
        prev["key"] = key
        prev["bucket"] = _prefer_bucket(str(prev.get("bucket") or ""), row["bucket"])
        if row["source"]:
            prev_src = str(prev.get("source") or "")
            # Upgrade empty / legacy abbreviated source labels.
            if not prev_src or prev_src == "roe":
                prev["source"] = row["source"]
        merged_props = dict(prev.get("props") or {})
        merged_props.update(row["props"])
        prev["props"] = merged_props
        prev["value"] = value
        assets[idx] = prev
        row = prev
    graph.assets = assets
    return row


def _compact_graph(graph: AssetGraph) -> None:
    """Merge legacy alias duplicates and rewire relations to canonical ids."""
    old_assets = list(graph.assets or [])
    old_relations = list(graph.relations or [])
    old_hubs = {
        str(a.get("id") or "")
        for a in old_assets
        if (a.get("props") or {}).get("center")
        or (a.get("props") or {}).get("kind") == "hub"
    }
    graph.assets = []
    graph.relations = []
    id_map: dict[str, str] = {}
    for asset in old_assets:
        row = _upsert_asset(
            graph,
            typ=str(asset.get("type") or "other"),
            value=str(asset.get("value") or ""),
            bucket=str(asset.get("bucket") or "discovered"),
            source=str(asset.get("source") or ""),
            props=asset.get("props") if isinstance(asset.get("props"), dict) else None,
        )
        if row is not None:
            id_map[str(asset.get("id") or "")] = str(row["id"])

    bucket_spokes = {"seed", "in_scope", "candidate", "exclusion", "discovered"}
    for edge in old_relations:
        # Remove legacy visual-only hub spokes. Structural edges survive.
        if (
            str(edge.get("source") or "") in old_hubs
            and str(edge.get("rel") or "") in bucket_spokes
        ):
            continue
        sid = id_map.get(str(edge.get("source") or ""), "")
        tid = id_map.get(str(edge.get("target") or ""), "")
        _upsert_relation(
            graph,
            rel=str(edge.get("rel") or "related"),
            source_id=sid,
            target_id=tid,
            props=edge.get("props") if isinstance(edge.get("props"), dict) else None,
        )


def _upsert_relation(
    graph: AssetGraph,
    *,
    rel: str,
    source_id: str,
    target_id: str,
    props: dict | None = None,
) -> dict[str, Any] | None:
    """Insert a typed edge. ``rel`` is a free-form slug."""
    rel_s = sanitize_label(rel, default="related", max_len=48) or "related"
    sid = (source_id or "").strip()
    tid = (target_id or "").strip()
    if not sid or not tid or sid == tid:
        return None
    rid = _stable_id("r", rel_s, sid, tid)
    relations = list(graph.relations or [])
    for existing in relations:
        if str(existing.get("id") or "") == rid:
            if props:
                merged = dict(existing.get("props") or {})
                merged.update(props)
                existing["props"] = merged
            graph.relations = relations
            return existing
        if (
            str(existing.get("rel") or "") == rel_s
            and str(existing.get("source") or "") == sid
            and str(existing.get("target") or "") == tid
        ):
            return existing
    row = {
        "id": rid,
        "rel": rel_s,
        "source": sid,
        "target": tid,
        "props": dict(props or {}),
    }
    relations.append(row)
    graph.relations = relations
    return row


_BUCKET_RANK = {
    "hub": 100,
    "in_scope": 80,
    "seed": 70,
    "exclusion": 60,
    "candidate": 50,
    "discovered": 40,
    "finding": 30,
    "other": 0,
}


def _prefer_bucket(prev: str, new: str) -> str:
    if _BUCKET_RANK.get(new, 0) >= _BUCKET_RANK.get(prev, 0):
        return new or prev or "discovered"
    return prev or new or "discovered"


def _ingest_assets(
    project: Project,
    assets: Iterable[dict[str, Any] | str],
    *,
    bucket: str = "discovered",
    source: str = "",
    relations: Iterable[dict[str, Any]] | None = None,
    save: bool = True,
) -> AssetGraph:
    """Producer API: upsert assets (+ optional relations) into the project graph."""
    graph = _get_or_create_graph(project)
    id_by_key: dict[str, str] = {
        str(a.get("key") or ""): str(a.get("id") or "")
        for a in (graph.assets or [])
        if a.get("key") and a.get("id")
    }
    for raw in assets or []:
        if isinstance(raw, str):
            accepted = accept_asset(raw) or {"type": "other", "value": raw.strip()}
            typ, value = accepted["type"], accepted["value"]
            props = None
        elif isinstance(raw, dict):
            typ = str(raw.get("type") or raw.get("asset_type") or "")
            value = str(raw.get("value") or raw.get("target") or "").strip()
            props = raw.get("props") if isinstance(raw.get("props"), dict) else None
            b = str(raw.get("bucket") or bucket)
            src = str(raw.get("source") or source)
            row = _upsert_asset(
                graph, typ=typ, value=value, bucket=b, source=src, props=props
            )
            if row:
                id_by_key[row["key"]] = row["id"]
            continue
        else:
            continue
        row = _upsert_asset(
            graph, typ=typ, value=value, bucket=bucket, source=source, props=props
        )
        if row:
            id_by_key[row["key"]] = row["id"]

    for edge in relations or []:
        if not isinstance(edge, dict):
            continue
        rel = str(edge.get("rel") or edge.get("type") or "related")
        src = str(edge.get("source") or edge.get("from") or "")
        tgt = str(edge.get("target") or edge.get("to") or "")
        # Allow key references: "type:value"
        if ":" in src and src not in {a.get("id") for a in (graph.assets or [])}:
            src = id_by_key.get(src.lower()) or src
        if ":" in tgt and tgt not in {a.get("id") for a in (graph.assets or [])}:
            tgt = id_by_key.get(tgt.lower()) or tgt
        _upsert_relation(
            graph,
            rel=rel,
            source_id=src,
            target_id=tgt,
            props=edge.get("props") if isinstance(edge.get("props"), dict) else None,
        )

    if save:
        graph.save()
    return graph


def _ip_in_net(ip_s: str, net_s: str) -> bool:
    try:
        return ipaddress.ip_address(ip_s) in ipaddress.ip_network(net_s, strict=False)
    except ValueError:
        return False


def _basename(path: str) -> str:
    raw = (path or "").replace("\\", "/").rstrip("/")
    return raw.rsplit("/", 1)[-1] if raw else ""


def _enrich_shape_relations(graph: AssetGraph) -> None:
    """Derive structural edges from value shapes (not type allowlists)."""
    assets = list(graph.assets or [])
    by_value: dict[str, list[dict]] = {}
    for a in assets:
        by_value.setdefault(str(a.get("value") or "").lower(), []).append(a)

    nets = [a for a in assets if detect_shape(str(a.get("value") or "")) == "netblock"]
    ips = [a for a in assets if detect_shape(str(a.get("value") or "")) == "ip"]
    urls = [
        a
        for a in assets
        if detect_shape(str(a.get("value") or "")) == "url"
        or sanitize_label(str(a.get("type") or "")) in {"url", "repo"}
    ]
    services = [
        a for a in assets if detect_shape(str(a.get("value") or "")) == "service"
    ]
    pathish = [
        a
        for a in assets
        if ("/" in str(a.get("value") or "") or "\\" in str(a.get("value") or ""))
        and detect_shape(str(a.get("value") or "")) != "url"
    ]
    digests = [a for a in assets if detect_shape(str(a.get("value") or "")) == "hash"]

    for net in nets:
        for ip in ips:
            if _ip_in_net(str(ip.get("value") or ""), str(net.get("value") or "")):
                _upsert_relation(
                    graph,
                    rel="contains",
                    source_id=str(net["id"]),
                    target_id=str(ip["id"]),
                )

    for url in urls:
        host = url_host(str(url.get("value") or ""))
        if not host:
            continue
        for peer in by_value.get(host.lower(), []):
            if peer["id"] == url["id"]:
                continue
            rel = (
                "ip_address"
                if detect_shape(str(peer.get("value") or "")) == "ip"
                else "domain"
            )
            _upsert_relation(
                graph, rel=rel, source_id=str(url["id"]), target_id=str(peer["id"])
            )

    for svc in services:
        endpoint = parse_service(str(svc.get("value") or ""))
        if endpoint is None:
            continue
        for peer in by_value.get(endpoint.host.lower(), []):
            _upsert_relation(
                graph,
                rel="port",
                source_id=str(peer["id"]),
                target_id=str(svc["id"]),
                props={
                    "port": endpoint.port,
                    **({"protocol": endpoint.protocol} if endpoint.protocol else {}),
                },
            )

    by_base: dict[str, list[dict]] = {}
    for art in pathish:
        base = _basename(str(art.get("value") or "")).lower()
        if base and "." in base:
            by_base.setdefault(base, []).append(art)
    for group in by_base.values():
        if len(group) < 2:
            continue
        root = group[0]
        for peer in group[1:]:
            _upsert_relation(
                graph,
                rel="same_file",
                source_id=str(root["id"]),
                target_id=str(peer["id"]),
            )

    for digest in digests:
        hv = str(digest.get("value") or "").lower()
        if len(hv) < 32:
            continue
        for art in pathish:
            if hv in str(art.get("value") or "").lower():
                _upsert_relation(
                    graph,
                    rel="hashes",
                    source_id=str(digest["id"]),
                    target_id=str(art["id"]),
                )


def _sync_roe(graph: AssetGraph, roe) -> None:
    if roe is None:
        return
    for bucket, attr in (
        ("seed", "seed"),
        ("in_scope", "in_scope"),
        ("candidate", "candidates"),
        ("exclusion", "exclusions"),
    ):
        for t in coerce_targets(getattr(roe, attr, None)):
            for related in SubjectCanonicalizer.expand(t):
                _upsert_asset(
                    graph,
                    typ=str(related.get("type") or "other"),
                    value=str(related.get("value") or ""),
                    bucket=bucket,
                    source="rules_of_engagement",
                )


def _finding_as_row(finding) -> dict[str, Any]:
    """Normalize a Finding ORM/row into the discovery-asset dict shape."""
    meta = getattr(finding, "metadata", None)
    if not isinstance(meta, dict):
        meta = {}
    subjects = getattr(finding, "subjects", None)
    if not isinstance(subjects, list):
        subjects = []
    return {
        "host": getattr(finding, "host", None) or "",
        "port": getattr(finding, "port", None),
        "protocol": getattr(finding, "protocol", None) or "",
        # evidence_path is provenance only — discovery ignores it.
        "evidence_path": getattr(finding, "evidence_path", None) or "",
        "asset_type": getattr(finding, "asset_type", None) or "",
        "kind": getattr(finding, "kind", None) or "",
        "title": getattr(finding, "title", None) or "",
        "subjects": subjects,
        "metadata": meta,
        "url": meta.get("url") or meta.get("uri") or "",
        "uri": meta.get("uri") or "",
        "repo": meta.get("repo") or "",
        "hash": meta.get("hash") or meta.get("sha256") or "",
        "sample": meta.get("sample") or meta.get("malware") or "",
        "malware": meta.get("malware") or "",
        "package": meta.get("package") or "",
    }


def _is_pollution_asset(asset: dict[str, Any]) -> bool:
    """FIND-XXX / finding:* nodes are board rows — not attack-surface subjects."""
    aid = str(asset.get("id") or "")
    key = str(asset.get("key") or "")
    value = str(asset.get("value") or "").strip()
    bucket = str(asset.get("bucket") or "")
    typ = str(asset.get("type") or "")
    props = asset.get("props") if isinstance(asset.get("props"), dict) else {}
    if bucket == "finding" or props.get("kind") == "finding":
        return True
    if aid.startswith("finding:") or key.startswith("finding:"):
        return True
    if typ in {"finding", "findings"}:
        return True
    if value.upper().startswith("FIND-") or value.upper() == "FIND":
        return True
    return False


def _prune_finding_pollution(graph: AssetGraph) -> None:
    """Drop FIND-* nodes and dangling edges from a previously polluted graph."""
    keep: list[dict[str, Any]] = []
    drop_ids: set[str] = set()
    for a in graph.assets or []:
        if _is_pollution_asset(a):
            drop_ids.add(str(a.get("id") or ""))
            continue
        keep.append(a)
    graph.assets = keep
    if not drop_ids:
        return
    graph.relations = [
        e
        for e in (graph.relations or [])
        if str(e.get("source") or "") not in drop_ids
        and str(e.get("target") or "") not in drop_ids
    ]


def _attach_finding_props(row: dict[str, Any], finding) -> None:
    """Annotate an asset with lightweight finding refs (no FIND-* graph nodes)."""
    props = dict(row.get("props") or {})
    refs = list(props.get("finding_refs") or [])
    seq = getattr(finding, "seq", None)
    ref = {
        "seq": seq,
        "title": (getattr(finding, "title", None) or "")[:200],
        "severity": getattr(finding, "severity", "") or "",
        "status": getattr(finding, "status", "") or "",
        "kind": sanitize_label(getattr(finding, "kind", None) or "", default="") or "",
    }
    # De-dupe by seq when present.
    refs = [
        r
        for r in refs
        if not (seq is not None and r.get("seq") == seq)
    ]
    refs.append(ref)
    props["finding_refs"] = refs[-12:]
    # Surface highest severity for UI chips without a FIND node.
    order = ("critical", "high", "medium", "low", "info")
    severities = [str(r.get("severity") or "").lower() for r in props["finding_refs"]]
    for sev in order:
        if sev in severities:
            props["severity"] = sev
            break
    if not props.get("title"):
        props["title"] = ref["title"]
    row["props"] = props


def _sync_finding(graph: AssetGraph, finding) -> None:
    """Promote finding *subjects* onto the graph — never FIND-XXX nodes."""
    finding_row = _finding_as_row(finding)
    subjects = SubjectCanonicalizer.from_finding(finding_row)
    if not subjects:
        return

    for item in subjects:
        typ = str(item.get("type") or "other")
        value = str(item.get("value") or "").strip()
        if not value:
            continue
        # Meaningful discovery stays as discovered (candidates come from RoE).
        row = _upsert_asset(
            graph,
            typ=typ,
            value=value,
            bucket="discovered",
            source="finding",
        )
        if row is None:
            continue
        if str(item.get("relation") or "affected") != "parent":
            _attach_finding_props(row, finding)


def _sync_project_graph(
    project: Project,
    *,
    findings: list | None = None,
    persist: bool = True,
    graph: AssetGraph | None = None,
) -> AssetGraph:
    """Rebuild/merge graph from RoE + finding subjects (idempotent upserts)."""
    graph = graph or _get_or_create_graph(project)
    _prune_finding_pollution(graph)
    _compact_graph(graph)
    roe = getattr(project, "roe", None)
    _sync_roe(graph, roe)
    for f in findings or []:
        _sync_finding(graph, f)
    _prune_finding_pollution(graph)
    _enrich_shape_relations(graph)

    # Hub: first seed else first in_scope (always the circle-layout center).
    assets = list(graph.assets or [])
    hub = next((a for a in assets if a.get("bucket") == "seed"), None)
    if hub is None:
        hub = next((a for a in assets if a.get("bucket") == "in_scope"), None)
    if hub is not None:
        for a in assets:
            if a.get("id") == hub["id"]:
                props = dict(a.get("props") or {})
                props["kind"] = "hub"
                props["center"] = True
                a["props"] = props
                a["bucket"] = a.get("bucket") or "seed"
    if persist:
        graph.save()
    return graph


def _view_payload(graph: AssetGraph) -> dict[str, Any]:
    """UI payload: meaningful assets only (no FIND-XXX board rows)."""
    structural_priority = {"port": 100, "contains": 80}
    parent_by_id: dict[str, tuple[int, str]] = {}
    for edge in graph.relations or []:
        rel = str(edge.get("rel") or "")
        priority = structural_priority.get(rel, 0)
        if not priority:
            continue
        child = str(edge.get("target") or "")
        parent = str(edge.get("source") or "")
        current = parent_by_id.get(child)
        if child and parent and child != parent and (
            current is None or priority > current[0]
        ):
            parent_by_id[child] = (priority, parent)

    nodes: list[dict[str, Any]] = []
    types: set[str] = set()
    buckets: set[str] = set()
    rels: set[str] = set()

    for a in graph.assets or []:
        if _is_pollution_asset(a):
            continue
        typ = str(a.get("type") or "other")
        value = str(a.get("value") or "")
        bucket = str(a.get("bucket") or "other")
        props = a.get("props") if isinstance(a.get("props"), dict) else {}
        kind = str(props.get("kind") or "asset")
        raw_source = str(a.get("source") or "")
        if raw_source in {"roe", "rules_of_engagement"}:
            display_source = "Rules of Engagement"
        elif raw_source == "finding":
            display_source = "Finding subject"
        else:
            display_source = raw_source
        types.add(typ)
        buckets.add(bucket)
        refs = props.get("finding_refs") or []
        nodes.append(
            {
                "id": str(a.get("id") or ""),
                "label": value[:48],
                "type": typ,
                "bucket": bucket,
                "kind": kind,
                "center": bool(props.get("center") or kind == "hub"),
                "value": value,
                "source": display_source,
                "title": str(props.get("title") or ""),
                "severity": str(props.get("severity") or ""),
                "status": str(props.get("status") or ""),
                "finding_count": len(refs) if isinstance(refs, list) else 0,
                "parent_id": parent_by_id.get(str(a.get("id") or ""), (0, ""))[1],
                "props": props,
            }
        )

    edges: list[dict[str, str]] = []
    node_ids = {n["id"] for n in nodes}
    for e in graph.relations or []:
        sid = str(e.get("source") or "")
        tid = str(e.get("target") or "")
        rel = str(e.get("rel") or "related")
        if sid not in node_ids or tid not in node_ids or sid == tid:
            continue
        rels.add(rel)
        edges.append({"source": sid, "target": tid, "rel": rel})

    return {
        "nodes": nodes,
        "edges": edges,
        "legend": {
            "types": sorted(types),
            "buckets": sorted(buckets),
            "relations": sorted(rels),
        },
    }


class AssetGraphEngine:
    """Project-bound canonical asset graph projection and persistence."""

    def __init__(self, project: Project) -> None:
        self.project = project

    @property
    def graph(self) -> AssetGraph:
        return _get_or_create_graph(self.project)

    def upsert_asset(self, **kwargs: Any) -> dict[str, Any] | None:
        return _upsert_asset(self.graph, **kwargs)

    def ingest(
        self,
        assets: Iterable[dict[str, Any] | str],
        *,
        bucket: str = "discovered",
        source: str = "",
        relations: Iterable[dict[str, Any]] | None = None,
        save: bool = True,
    ) -> AssetGraph:
        return _ingest_assets(
            self.project,
            assets,
            bucket=bucket,
            source=source,
            relations=relations,
            save=save,
        )

    def sync(
        self,
        *,
        findings: list | None = None,
        persist: bool = True,
        rebuild: bool = False,
    ) -> AssetGraph:
        graph = self.graph
        if rebuild:
            graph.assets = []
            graph.relations = []
        return _sync_project_graph(
            self.project,
            findings=findings,
            persist=persist,
            graph=graph,
        )

    def rebuild(self, *, persist: bool = True) -> AssetGraph:
        findings = list(self.project.findings.order_by("seq", "created_at"))
        return self.sync(findings=findings, persist=persist, rebuild=True)

    def is_stale(self) -> bool:
        graph = self.graph
        latest_finding = (
            self.project.findings.order_by("-updated_at")
            .values_list("updated_at", flat=True)
            .first()
        )
        roe = getattr(self.project, "roe", None)
        source_times = [
            value
            for value in (latest_finding, getattr(roe, "updated_at", None))
            if value is not None
        ]
        return bool(source_times and graph.updated_at < max(source_times))

    def payload(self, *, fresh: bool = False) -> dict[str, Any]:
        graph = self.rebuild() if fresh and self.is_stale() else self.graph
        return _view_payload(graph)

    def engagement(
        self,
        *,
        findings: list | None = None,
        persist: bool = True,
        rebuild: bool = False,
    ) -> dict[str, Any]:
        graph = self.sync(
            findings=findings,
            persist=persist,
            rebuild=rebuild,
        )
        return _view_payload(graph)
