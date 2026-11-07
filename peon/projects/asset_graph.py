"""OAM-inspired engagement asset graph store (open types & relations).

Producers (RoE, findings, roles, operator, tools) upsert assets and typed
edges. Types and ``rel`` labels are free-form slugs — the UI must render
unknown kinds without a Python/JS allowlist.

RoE buckets remain the authorization overlay; this graph is the engagement
surface SoT for visualization and future enrichment.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterable
from typing import Any

from django.utils import timezone as dj_tz

from peon.projects.asset_model import (
    classify,
    relation_status,
    relation_title,
)
from peon.projects.models import AssetGraph, Project
from peon.projects.target_discovery import subjects_from_finding
from peon.projects.targets import (
    accept_asset,
    canonical_asset_key,
    coerce_targets,
    hostname_of,
    normalize_ip,
    parse_service,
    prefer_by_rank,
    prefer_producer_type,
    sanitize_label,
    split_typed_line,
)

# Sources whose rows are re-derived from RoE/findings on every rebuild. Anything
# else (agents, tools, operator) is durable producer data and survives rebuilds.
_DERIVED_SOURCES = frozenset({"rules_of_engagement", "roe", "finding", ""})
_MAX_SOURCES = 8
_MAX_INGEST_ASSETS = 500
_MAX_INGEST_RELATIONS = 1000


def _now() -> str:
    return dj_tz.now().isoformat(timespec="seconds")


class _Index:
    """In-memory lookup for one graph (O(1) upserts instead of list scans)."""

    __slots__ = ("assets", "relations", "by_id", "by_key", "rel_ids", "n_assets", "n_rels")

    def __init__(self, graph: AssetGraph) -> None:
        self.assets = graph.assets
        self.relations = graph.relations
        self.by_id = {str(a.get("id") or ""): a for a in graph.assets}
        self.by_key = {str(a.get("key") or ""): a for a in graph.assets if a.get("key")}
        self.rel_ids = {str(r.get("id") or ""): r for r in graph.relations}
        self.n_assets = len(graph.assets)
        self.n_rels = len(graph.relations)


def _index(graph: AssetGraph) -> _Index:
    if not isinstance(graph.assets, list):
        graph.assets = []
    if not isinstance(graph.relations, list):
        graph.relations = []
    idx = graph.__dict__.get("_oag_index")
    if (
        idx is None
        or idx.assets is not graph.assets
        or idx.relations is not graph.relations
        or idx.n_assets != len(graph.assets)
        or idx.n_rels != len(graph.relations)
    ):
        idx = _Index(graph)
        graph.__dict__["_oag_index"] = idx
    return idx


def _touch_source(row: dict[str, Any], name: str, now: str) -> None:
    """Record that ``name`` (re)observed this asset (OAM-style provenance).

    Empty names are skipped — they would stamp ``""`` into ``sources`` and
    make the row look derived (``"" ∈ _DERIVED_SOURCES``), wiping durable
    producer assets on the next rebuild.
    """
    name = (name or "").strip()[:120]
    row.setdefault("first_seen", now)
    row["last_seen"] = now
    if not name:
        return
    sources = list(row.get("sources") or [])
    for entry in sources:
        if entry.get("name") == name:
            if entry.get("last_seen") != now:
                entry["last_seen"] = now
                entry["count"] = int(entry.get("count") or 1) + 1
            break
    else:
        sources.append({"name": name, "first_seen": now, "last_seen": now, "count": 1})
    row["sources"] = sources[-_MAX_SOURCES:]


def _stable_id(*parts: str) -> str:
    raw = "|".join((p or "").strip().lower() for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _merge_type_labels(row: dict[str, Any], typ: str) -> None:
    """Accumulate producer type slugs seen for this label on ``types``."""
    primary = prefer_producer_type(str(row.get("type") or ""), typ)
    row["type"] = primary
    seen: list[str] = []
    for t in list(row.get("types") or []) + [primary, typ]:
        s = sanitize_label(str(t or ""))
        if s and s not in seen and s != "other":
            seen.append(s)
    if primary and primary not in seen:
        seen.insert(0, primary)
    row["types"] = seen[:8]


def _get_json_graph(project: Project) -> AssetGraph:
    """Load or create the per-project ``AssetGraph`` JSON row (sole store)."""

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


def _with_service_protocol(props: dict[str, Any], typ: str, value: str) -> dict[str, Any]:
    """Fold a service value's protocol into this node's accumulated set.

    ``canonical_asset_key`` makes host:port the identity, so the same socket
    named with and without a protocol — or with two different protocols,
    e.g. DNS's tcp/53 and udp/53 — lands on one node; nothing about *that*
    discovery is lost, it just moves from the identity to this property.
    """
    if typ != "service":
        return props
    endpoint = parse_service(value)
    if endpoint is None or not endpoint.protocol:
        return props
    protocols = set(props.get("protocols") or [])
    protocols.add(endpoint.protocol)
    out = dict(props)
    out["protocols"] = sorted(protocols)
    return out


def _richer_service_value(prev_value: str, new_value: str, typ: str) -> str:
    """Keep whichever of two same-identity values already names a protocol."""
    if typ != "service":
        return new_value
    prev_endpoint = parse_service(prev_value)
    if prev_endpoint is not None and prev_endpoint.protocol:
        return prev_value
    return new_value


def _upsert_asset(
    graph: AssetGraph,
    *,
    typ: str,
    value: str,
    bucket: str = "discovered",
    source: str = "",
    props: dict | None = None,
) -> dict[str, Any] | None:
    """Insert or merge one asset. Exact label → one node; combine provenance."""
    accepted = accept_asset(value, typ)
    if accepted is None:
        return None
    typ = accepted["type"]
    value = accepted["value"]
    # Identity is the label (value), not type — same string merges.
    key = canonical_asset_key(typ, value)
    nid = _stable_id("a", key)
    idx = _index(graph)
    now = _now()
    prev = idx.by_key.get(key) or idx.by_id.get(nid)
    bucket_s = sanitize_label(bucket, default="discovered") or "discovered"
    if prev is None:
        row: dict[str, Any] = {
            "id": nid,
            "type": typ,
            "types": [typ] if typ and typ != "other" else [],
            "value": value,
            "key": key,
            "bucket": bucket_s,
            "source": (source or "")[:120],
            "props": _with_service_protocol(dict(props or {}), typ, value),
        }
        _touch_source(row, source, now)
        graph.assets.append(row)
        idx.by_id[nid] = row
        idx.by_key[key] = row
        idx.n_assets = len(graph.assets)
        return row

    _merge_type_labels(prev, typ)
    prev["key"] = key
    prev["bucket"] = _prefer_bucket(str(prev.get("bucket") or ""), bucket_s)
    if source:
        prev_src = str(prev.get("source") or "")
        if not prev_src or prev_src == "roe":
            prev["source"] = source[:120]
    if source or not prev.get("sources"):
        _touch_source(prev, source or prev.get("source") or "", now)
    merged = dict(prev.get("props") or {})
    merged.update(props or {})
    merged = _with_service_protocol(merged, typ, value)
    prev["props"] = merged
    prev["value"] = _richer_service_value(str(prev.get("value") or ""), value, typ)
    pid = str(prev.get("id") or nid)
    idx.by_id[pid] = prev
    idx.by_key[key] = prev
    return prev


def _compact_graph(graph: AssetGraph) -> None:
    """Merge nodes that share an exact label; combine types/sources/props."""
    old_assets = list(graph.assets or [])
    old_relations = list(graph.relations or [])
    old_hubs = {
        str(a.get("id") or "")
        for a in old_assets
        if (a.get("props") or {}).get("center") or (a.get("props") or {}).get("kind") == "hub"
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
        if row is None:
            continue
        id_map[str(asset.get("id") or "")] = str(row["id"])
        for t in asset.get("types") or []:
            _merge_type_labels(row, str(t))
        # Carry provenance across the merge (compaction is not an observation).
        for src in asset.get("sources") or []:
            if not isinstance(src, dict) or not src.get("name"):
                continue
            have = {e.get("name"): e for e in row.get("sources") or []}
            if src["name"] not in have:
                row.setdefault("sources", []).append(dict(src))
        if asset.get("first_seen"):
            row["first_seen"] = min(
                str(row.get("first_seen") or asset["first_seen"]), str(asset["first_seen"])
            )
        if asset.get("last_seen"):
            row["last_seen"] = max(
                str(row.get("last_seen") or asset["last_seen"]), str(asset["last_seen"])
            )
        if row.get("sources"):
            row["sources"] = row["sources"][-_MAX_SOURCES:]

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
    derived: bool = False,
) -> dict[str, Any] | None:
    """Insert a typed, directed edge between two existing assets.

    ``rel`` is a free-form slug (OAM labels are recognised, others are
    ``custom``). Edges whose endpoints are not in the graph are refused rather
    than stored dangling.
    """
    rel_s = sanitize_label(rel, default="related", max_len=48) or "related"
    sid = (source_id or "").strip()
    tid = (target_id or "").strip()
    if not sid or not tid or sid == tid:
        return None
    idx = _index(graph)
    if sid not in idx.by_id or tid not in idx.by_id:
        return None
    rid = _stable_id("r", rel_s, sid, tid)
    extra = dict(props or {})
    if derived:
        extra["derived"] = True
    existing = idx.rel_ids.get(rid)
    if existing is not None:
        if extra:
            merged = dict(existing.get("props") or {})
            merged.update(extra)
            existing["props"] = merged
        return existing
    row = {"id": rid, "rel": rel_s, "source": sid, "target": tid, "props": extra}
    graph.relations.append(row)
    idx.rel_ids[rid] = row
    idx.n_rels = len(graph.relations)
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
    return prefer_by_rank(prev, new, _BUCKET_RANK, default="discovered")


def _resolve_ref(graph: AssetGraph, ref: Any) -> str:
    """Resolve an edge endpoint to an asset id, or ``""``.

    Accepts an asset id, ``{"type","value"}``, a typed ``"type:value"`` string,
    or a bare value that matches exactly one asset.
    """
    idx = _index(graph)
    if isinstance(ref, dict):
        typ = str(ref.get("type") or ref.get("asset_type") or "")
        val = str(ref.get("value") or ref.get("target") or "").strip()
        if not val:
            return ""
        row = idx.by_key.get(canonical_asset_key(typ, val))
        return str(row["id"]) if row else ""
    text = str(ref or "").strip()
    if not text:
        return ""
    if text in idx.by_id:
        return text
    typed = split_typed_line(text)
    if typed:
        row = idx.by_key.get(canonical_asset_key(*typed))
        if row:
            return str(row["id"])
    matches = [a for a in graph.assets if str(a.get("value") or "").lower() == text.lower()]
    return str(matches[0]["id"]) if len(matches) == 1 else ""


def _ref_asset(ref: Any) -> dict[str, str] | None:
    """Typed endpoint → asset dict so a relation can create its endpoints."""
    if isinstance(ref, dict):
        typ = str(ref.get("type") or ref.get("asset_type") or "")
        val = str(ref.get("value") or ref.get("target") or "").strip()
        return {"type": typ, "value": val} if val else None
    typed = split_typed_line(str(ref or "").strip())
    if typed:
        return {"type": typed[0], "value": typed[1]}
    return None


def _ingest_assets(
    project: Project,
    assets: Iterable[dict[str, Any] | str],
    *,
    bucket: str = "discovered",
    source: str = "",
    relations: Iterable[dict[str, Any]] | None = None,
    save: bool = True,
    report: dict[str, Any] | None = None,
) -> AssetGraph:
    """Producer API: upsert assets (+ optional relations) into the project graph.

    Relation endpoints may be typed references; a typed endpoint that is not in
    the graph yet is created (a DNS answer creates its IP, as in Amass).
    ``report`` (if given) receives ``assets``/``relations`` counts and
    ``skipped`` reasons.
    """
    graph = _get_json_graph(project)
    rep = report if report is not None else {}
    rep.setdefault("assets", 0)
    rep.setdefault("relations", 0)
    rep.setdefault("skipped", [])

    def add(typ: str, value: str, b: str, src: str, props: dict | None) -> dict | None:
        row = _upsert_asset(graph, typ=typ, value=value, bucket=b, source=src, props=props)
        if row is not None:
            rep["assets"] += 1
        return row

    for n, raw in enumerate(assets or []):
        if n >= _MAX_INGEST_ASSETS:
            rep["skipped"].append(f"asset limit {_MAX_INGEST_ASSETS} reached")
            break
        if isinstance(raw, str):
            typed = split_typed_line(raw.strip())
            accepted = accept_asset(typed[1], typed[0]) if typed else accept_asset(raw)
            if accepted is None or not add(
                accepted["type"], accepted["value"], bucket, source, None
            ):
                rep["skipped"].append(f"empty asset: {raw[:40]!r}")
        elif isinstance(raw, dict):
            typ = str(raw.get("type") or raw.get("asset_type") or "")
            value = str(raw.get("value") or raw.get("target") or "").strip()
            props = raw.get("props") if isinstance(raw.get("props"), dict) else None
            if not add(
                typ,
                value,
                str(raw.get("bucket") or bucket),
                str(raw.get("source") or source),
                props,
            ):
                rep["skipped"].append(f"empty asset: {str(raw)[:60]}")

    for n, edge in enumerate(relations or []):
        if n >= _MAX_INGEST_RELATIONS:
            rep["skipped"].append(f"relation limit {_MAX_INGEST_RELATIONS} reached")
            break
        if not isinstance(edge, dict):
            continue
        rel = str(edge.get("rel") or edge.get("type") or edge.get("label") or "related")
        ends = []
        for side in (
            edge.get("source", edge.get("from")),
            edge.get("target", edge.get("to")),
        ):
            rid = _resolve_ref(graph, side)
            if not rid:
                created = _ref_asset(side)
                if created:
                    row = add(created["type"], created["value"], bucket, source, None)
                    rid = str(row["id"]) if row else ""
            ends.append(rid)
        sid, tid = ends
        if not sid or not tid:
            rep["skipped"].append(f"unresolved relation endpoint: {rel}")
            continue
        props = dict(edge.get("props") or {}) if isinstance(edge.get("props"), dict) else {}
        if source:
            props.setdefault("source", source[:120])
        if _upsert_relation(graph, rel=rel, source_id=sid, target_id=tid, props=props or None):
            rep["relations"] += 1

    if save:
        graph.save()
    return graph


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
            _upsert_asset(
                graph,
                typ=str(t.get("type") or "other"),
                value=str(t.get("value") or ""),
                bucket=bucket,
                source="rules_of_engagement",
            )


def _link_embedded_relations(graph: AssetGraph) -> None:
    """Derive edges from embedded value contracts — not type allowlists.

    - ``service`` rows (producer-typed) use ``host:port[/proto]`` → ``port``
      edge to any existing asset whose value equals that host.
    - Values with a URL scheme → edge to assets whose value equals the hostname.
    - CIDR values → ``contains`` to assets whose value is an IP inside them.

    Never invents nodes; only links what producers already put on the graph.
    """
    import ipaddress

    assets = list(graph.assets or [])
    by_value: dict[str, list[dict[str, Any]]] = {}
    for a in assets:
        by_value.setdefault(str(a.get("value") or "").strip().lower(), []).append(a)

    for a in assets:
        aid = str(a.get("id") or "")
        val = str(a.get("value") or "").strip()
        typ = sanitize_label(str(a.get("type") or ""))
        if not aid or not val:
            continue

        if typ == "service":
            endpoint = parse_service(val)
            if endpoint is not None:
                for peer in by_value.get(endpoint.host.lower(), []):
                    pid = str(peer.get("id") or "")
                    if not pid or pid == aid:
                        continue
                    props: dict[str, Any] = {"port": endpoint.port, "derived": True}
                    if endpoint.protocol:
                        props["protocol"] = endpoint.protocol
                    _upsert_relation(
                        graph,
                        rel="port",
                        source_id=pid,
                        target_id=aid,
                        props=props,
                        derived=True,
                    )

        host = ""
        if "://" in val or typ in {"url", "uri", "repo", "repository"}:
            host = hostname_of(val)
        if host:
            for peer in by_value.get(host.lower(), []):
                pid = str(peer.get("id") or "")
                if not pid or pid == aid:
                    continue
                rel = (
                    "ip_address"
                    if normalize_ip(str(peer.get("value") or ""))
                    else "domain"
                )
                _upsert_relation(
                    graph,
                    rel=rel,
                    source_id=aid,
                    target_id=pid,
                    props={"derived": True},
                    derived=True,
                )

    for a in assets:
        raw = str(a.get("value") or "").strip()
        if "/" not in raw:
            continue
        try:
            net = ipaddress.ip_network(raw, strict=False)
        except ValueError:
            continue
        aid = str(a.get("id") or "")
        for peer in assets:
            pid = str(peer.get("id") or "")
            if not pid or pid == aid:
                continue
            ip = normalize_ip(str(peer.get("value") or ""))
            if ip is None:
                continue
            try:
                if ipaddress.ip_address(ip) in net:
                    _upsert_relation(
                        graph,
                        rel="contains",
                        source_id=aid,
                        target_id=pid,
                        props={"derived": True},
                        derived=True,
                    )
            except ValueError:
                continue


def finding_subject_row(finding) -> dict[str, Any]:
    """Normalize a Finding ORM/row for subject discovery (subjects + host/port)."""
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
        "asset_type": getattr(finding, "asset_type", None) or "",
        "kind": getattr(finding, "kind", None) or "",
        "title": getattr(finding, "title", None) or "",
        "subjects": subjects,
        "metadata": meta,
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
    return bool(value.upper().startswith("FIND-") or value.upper() == "FIND")


def _drop_assets(graph: AssetGraph, predicate) -> None:
    """Remove assets matching ``predicate`` and any edges that touched them."""
    keep: list[dict[str, Any]] = []
    drop_ids: set[str] = set()
    for a in graph.assets or []:
        if predicate(a):
            drop_ids.add(str(a.get("id") or ""))
            continue
        keep.append(a)
    if not drop_ids:
        return
    graph.assets = keep
    graph.relations = [
        e
        for e in (graph.relations or [])
        if str(e.get("source") or "") not in drop_ids and str(e.get("target") or "") not in drop_ids
    ]


def _prune_finding_pollution(graph: AssetGraph) -> None:
    _drop_assets(graph, _is_pollution_asset)


def _prune_invalid_services(graph: AssetGraph) -> None:
    _drop_assets(
        graph,
        lambda a: str(a.get("type") or "") == "service"
        and parse_service(str(a.get("value") or "")) is None,
    )


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
    refs = [r for r in refs if not (seq is not None and r.get("seq") == seq)]
    refs.append(ref)
    props["finding_refs"] = refs[-12:]
    # Surface highest severity for UI chips without a FIND node.
    from peon.projects.findings import SEVERITY_ORDER

    severities = [str(r.get("severity") or "").lower() for r in props["finding_refs"]]
    for sev in SEVERITY_ORDER:
        if sev in severities:
            props["severity"] = sev
            break
    if not props.get("title"):
        props["title"] = ref["title"]
    row["props"] = props


def _sync_finding(graph: AssetGraph, finding) -> None:
    """Promote finding *subjects* onto the graph — never FIND-XXX nodes."""
    finding_row = finding_subject_row(finding)
    subjects = subjects_from_finding(finding_row)
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
    restore=None,
    first_seen: dict[str, str] | None = None,
) -> AssetGraph:
    """Rebuild/merge graph from RoE + finding subjects (idempotent upserts)."""
    graph = graph or _get_json_graph(project)
    # Exact projection: clear prior RoE/finding rows before re-applying sources.
    _reset_derived_projection(graph)
    _prune_finding_pollution(graph)
    _prune_invalid_services(graph)
    _compact_graph(graph)
    roe = getattr(project, "roe", None)
    _sync_roe(graph, roe)
    for f in findings or []:
        _sync_finding(graph, f)
    _prune_finding_pollution(graph)
    _prune_invalid_services(graph)
    if restore is not None:
        restore(graph)
    _link_embedded_relations(graph)

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
    for a in graph.assets or []:
        prior = (first_seen or {}).get(str(a.get("key") or ""))
        if prior:
            a["first_seen"] = min(str(a.get("first_seen") or prior), prior)
    _prune_dangling(graph)
    if persist:
        graph.save()
    return graph


def _view_payload(graph: AssetGraph) -> dict[str, Any]:
    """UI payload: meaningful assets only (no FIND-XXX board rows)."""
    nodes: list[dict[str, Any]] = []
    class_by_id: dict[str, Any] = {}

    for a in graph.assets or []:
        if _is_pollution_asset(a):
            continue
        typ = str(a.get("type") or "other")
        value = str(a.get("value") or "")
        bucket = str(a.get("bucket") or "other")
        props = a.get("props") if isinstance(a.get("props"), dict) else {}
        kind = str(props.get("kind") or "asset")
        cls = classify(typ)
        class_by_id[str(a.get("id") or "")] = cls
        refs = props.get("finding_refs") or []
        type_labels = [
            sanitize_label(str(t))
            for t in (a.get("types") or [typ])
            if sanitize_label(str(t))
        ]
        if typ and typ not in type_labels:
            type_labels.insert(0, typ)
        nodes.append(
            {
                "id": str(a.get("id") or ""),
                "label": value[:48],
                "type": typ,
                "types": type_labels[:8],
                "class_label": cls.label,
                "oam": cls.oam,
                "bucket": bucket,
                "center": bool(props.get("center") or kind == "hub"),
                "value": value,
                "sources": [
                    {
                        "name": str(s.get("name") or ""),
                        "first_seen": str(s.get("first_seen") or ""),
                        "last_seen": str(s.get("last_seen") or ""),
                        "count": int(s.get("count") or 1),
                    }
                    for s in (a.get("sources") or [])
                    if isinstance(s, dict)
                ],
                "first_seen": str(a.get("first_seen") or ""),
                "last_seen": str(a.get("last_seen") or ""),
                "title": str(props.get("title") or ""),
                "severity": str(props.get("severity") or ""),
                "status": str(props.get("status") or ""),
                "finding_count": len(refs) if isinstance(refs, list) else 0,
            }
        )

    edges: list[dict[str, Any]] = []
    rel_counts: dict[str, dict[str, Any]] = {}
    degree: dict[str, int] = {}
    node_ids = {n["id"] for n in nodes}
    for e in graph.relations or []:
        sid = str(e.get("source") or "")
        tid = str(e.get("target") or "")
        rel = str(e.get("rel") or "related")
        if sid not in node_ids or tid not in node_ids or sid == tid:
            continue
        status = relation_status(rel, class_by_id[sid], class_by_id[tid])
        entry = rel_counts.setdefault(
            rel, {"label": rel, "title": relation_title(rel), "count": 0, "status": status}
        )
        entry["count"] += 1
        if status == "unexpected":
            entry["status"] = "unexpected"
        degree[sid] = degree.get(sid, 0) + 1
        degree[tid] = degree.get(tid, 0) + 1
        edges.append(
            {
                "source": sid,
                "target": tid,
                "rel": rel,
                "title": relation_title(rel),
                "status": status,
                "derived": bool((e.get("props") or {}).get("derived")),
            }
        )
    for n in nodes:
        n["degree"] = degree.get(n["id"], 0)

    # Filter chips = producer types present on the graph (open vocabulary).
    type_counts: dict[str, int] = {}
    for n in nodes:
        labels = n.get("types") or [n.get("type") or "other"]
        for t in labels:
            slug = str(t or "other")
            type_counts[slug] = type_counts.get(slug, 0) + 1

    return {
        "nodes": nodes,
        "edges": edges,
        "legend": {
            # Producer types present on the graph (open vocabulary).
            "kinds": sorted(
                (
                    {
                        "slug": typ,
                        "label": classify(typ).label,
                        "count": n,
                    }
                    for typ, n in type_counts.items()
                ),
                key=lambda g: (-g["count"], g["slug"]),
            ),
            "relation_counts": sorted(rel_counts.values(), key=lambda r: -r["count"]),
        },
        "stats": {"nodes": len(nodes), "edges": len(edges)},
        "store": "project",
    }


def _is_derived_asset(row: dict[str, Any]) -> bool:
    names = {
        str(s.get("name") or "") for s in (row.get("sources") or []) if isinstance(s, dict)
    } or {str(row.get("source") or "")}
    return names <= _DERIVED_SOURCES


def _reset_derived_projection(graph: AssetGraph) -> None:
    """Drop RoE/finding projection rows so the next sync is exact, not a union.

    Stale derived keys otherwise survive forever because upsert never deletes
    obsolete rows. Durable producer rows (agents/tools/operator) are kept.
    """
    keep = [a for a in (graph.assets or []) if not _is_derived_asset(a)]
    keep_ids = {str(a.get("id") or "") for a in keep}
    graph.assets = keep
    graph.relations = [
        r
        for r in (graph.relations or [])
        if not (r.get("props") or {}).get("derived")
        and str(r.get("source") or "") in keep_ids
        and str(r.get("target") or "") in keep_ids
    ]


def _producer_snapshot(graph: AssetGraph):
    """Capture durable producer data before a rebuild wipes derived rows.

    Returns ``(restore, first_seen)``. ``restore(graph)`` re-applies producer
    assets (with their provenance) and relations onto the freshly derived
    graph; edges whose endpoints no longer exist are dropped.
    """
    assets = [dict(a) for a in graph.assets or [] if not _is_derived_asset(a)]
    relations = [
        dict(r) for r in graph.relations or [] if not (r.get("props") or {}).get("derived")
    ]
    first_seen: dict[str, str] = {}
    for a in graph.assets or []:
        if not a.get("first_seen"):
            continue
        # Index by current + legacy type:value keys so rebuild preserves timestamps
        # across the label-identity migration.
        keys = {
            str(a.get("key") or ""),
            canonical_asset_key(str(a.get("type") or ""), str(a.get("value") or "")),
        }
        stamp = str(a["first_seen"])
        for k in keys:
            if not k:
                continue
            prev = first_seen.get(k)
            first_seen[k] = min(prev, stamp) if prev else stamp
    old_ids = {str(a.get("id") or ""): a for a in graph.assets or []}

    def restore(target: AssetGraph) -> None:
        id_map: dict[str, str] = {}
        def _restore_asset(a: dict[str, Any]) -> dict[str, Any] | None:
            # Preserve producer provenance so rebuild does not reclassify as derived.
            row = _upsert_asset(
                target,
                typ=str(a.get("type") or "other"),
                value=str(a.get("value") or ""),
                bucket=str(a.get("bucket") or "discovered"),
                source=str(a.get("source") or ""),
                props=a.get("props") if isinstance(a.get("props"), dict) else None,
            )
            if row is None:
                return None
            for t in a.get("types") or []:
                _merge_type_labels(row, str(t))
            have = {e.get("name") for e in row.get("sources") or []}
            for src in a.get("sources") or []:
                if isinstance(src, dict) and src.get("name") and src["name"] not in have:
                    row.setdefault("sources", []).append(dict(src))
            row["sources"] = (row.get("sources") or [])[-_MAX_SOURCES:]
            if a.get("source") and not row.get("source"):
                row["source"] = a["source"]
            return row

        for a in assets:
            row = _restore_asset(a)
            if row is None:
                continue
            id_map[str(a.get("id") or "")] = str(row["id"])
        for r in relations:
            sid, tid = str(r.get("source") or ""), str(r.get("target") or "")
            # Endpoints may be producer rows (remapped) or derived rows that the
            # rebuild just re-created under the same stable key-derived id.
            sid = id_map.get(sid) or sid
            tid = id_map.get(tid) or tid
            if sid not in _index(target).by_id and sid in old_ids:
                row = _restore_asset(old_ids[sid])
                sid = str(row["id"]) if row else ""
            if tid not in _index(target).by_id and tid in old_ids:
                row = _restore_asset(old_ids[tid])
                tid = str(row["id"]) if row else ""
            _upsert_relation(
                target,
                rel=str(r.get("rel") or "related"),
                source_id=sid,
                target_id=tid,
                props=r.get("props") if isinstance(r.get("props"), dict) else None,
            )

    return restore, first_seen


def _prune_dangling(graph: AssetGraph) -> None:
    ids = {str(a.get("id") or "") for a in graph.assets or []}
    graph.relations = [
        r
        for r in graph.relations or []
        if str(r.get("source") or "") in ids and str(r.get("target") or "") in ids
    ]


class AssetGraphEngine:
    """Project-bound canonical asset graph projection and persistence."""

    def __init__(self, project: Project) -> None:
        self.project = project

    @property
    def graph(self) -> AssetGraph:
        return _get_json_graph(self.project)

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
        restore = first_seen = None
        if rebuild:
            # Derived rows (RoE/findings) are recomputed; producer data
            # (agents, tools, operator) is durable and re-applied on top.
            restore, first_seen = _producer_snapshot(graph)
            graph.assets = []
            graph.relations = []
        return _sync_project_graph(
            self.project,
            findings=findings,
            persist=persist,
            graph=graph,
            restore=restore,
            first_seen=first_seen,
        )

    def record(
        self,
        assets: Iterable[dict[str, Any] | str] | None = None,
        relations: Iterable[dict[str, Any]] | None = None,
        *,
        source: str,
        bucket: str = "discovered",
    ) -> dict[str, Any]:
        """Producer entry point for agents/tools. Returns an ingest report."""
        report: dict[str, Any] = {}
        graph = _ingest_assets(
            self.project,
            assets or [],
            bucket=bucket,
            source=source,
            relations=relations,
            save=False,
            report=report,
        )
        graph.save()
        report["nodes"] = len(graph.assets or [])
        report["edges"] = len(graph.relations or [])
        return report


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
        if fresh and self.is_stale():
            findings = list(self.project.findings.order_by("seq", "created_at"))
            graph = self.sync(findings=findings, persist=True, rebuild=True)
        else:
            graph = self.graph
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
