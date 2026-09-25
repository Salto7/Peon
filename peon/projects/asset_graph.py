"""OAM-inspired engagement asset graph store (open types & relations).

Producers (RoE, findings, skills, operator, tools) upsert assets and typed
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
from peon.projects.targets import (
    accept_asset,
    coerce_targets,
    detect_shape,
    expand_related_assets,
    sanitize_label,
    url_host,
)


def _stable_id(*parts: str) -> str:
    raw = "|".join((p or "").strip().lower() for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def asset_key(typ: str, value: str) -> str:
    return f"{sanitize_label(typ, default='other')}:{(value or '').strip().lower()}"[:400]


def get_or_create_graph(project: Project) -> AssetGraph:
    graph, _ = AssetGraph.objects.get_or_create(project=project)
    return graph


def upsert_asset(
    graph: AssetGraph,
    *,
    typ: str,
    value: str,
    bucket: str = "discovered",
    source: str = "",
    props: dict | None = None,
    asset_id: str | None = None,
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
    typ = accepted["type"]
    value = accepted["value"]
    key = asset_key(typ, value)
    nid = asset_id or _stable_id("a", key)
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
        if prev.get("type") in {"", "other"} and row["type"] not in {"", "other"}:
            prev["type"] = row["type"]
            prev["key"] = asset_key(prev["type"], prev.get("value") or value)
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


def upsert_relation(
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


def ingest_assets(
    project: Project,
    assets: Iterable[dict[str, Any] | str],
    *,
    bucket: str = "discovered",
    source: str = "",
    relations: Iterable[dict[str, Any]] | None = None,
    save: bool = True,
) -> AssetGraph:
    """Producer API: upsert assets (+ optional relations) into the project graph."""
    graph = get_or_create_graph(project)
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
            row = upsert_asset(
                graph, typ=typ, value=value, bucket=b, source=src, props=props
            )
            if row:
                id_by_key[row["key"]] = row["id"]
            continue
        else:
            continue
        row = upsert_asset(
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
        upsert_relation(
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
                upsert_relation(
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
            upsert_relation(
                graph, rel=rel, source_id=str(url["id"]), target_id=str(peer["id"])
            )

    for svc in services:
        raw = str(svc.get("value") or "")
        if ":" not in raw:
            continue
        host = raw.rsplit(":", 1)[0]
        for peer in by_value.get(host.lower(), []):
            upsert_relation(
                graph,
                rel="port",
                source_id=str(peer["id"]),
                target_id=str(svc["id"]),
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
            upsert_relation(
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
                upsert_relation(
                    graph,
                    rel="hashes",
                    source_id=str(digest["id"]),
                    target_id=str(art["id"]),
                )


def sync_roe(graph: AssetGraph, roe) -> None:
    if roe is None:
        return
    for bucket, attr in (
        ("seed", "seed"),
        ("in_scope", "in_scope"),
        ("candidate", "candidates"),
        ("exclusion", "exclusions"),
    ):
        for t in coerce_targets(getattr(roe, attr, None)):
            for related in expand_related_assets(t):
                upsert_asset(
                    graph,
                    typ=str(related.get("type") or "other"),
                    value=str(related.get("value") or ""),
                    bucket=bucket,
                    source="rules_of_engagement",
                )


def sync_finding(graph: AssetGraph, finding) -> None:
    """Upsert finding node + subject asset + finding edge."""
    host = (getattr(finding, "host", None) or "").strip()
    evidence_path = (getattr(finding, "evidence_path", None) or "").strip()
    asset_type = sanitize_label(getattr(finding, "asset_type", None) or "", default="")
    meta = getattr(finding, "metadata", None)
    if not isinstance(meta, dict):
        meta = {}

    subject = ""
    subj_type = asset_type or "other"
    for key, hint in (
        ("subject", asset_type),
        ("asset", asset_type),
        ("file", "file"),
        ("path", "file"),
        ("sample", "sample"),
        ("malware", "malware"),
        ("hash", "hash"),
        ("sha256", "hash"),
        ("repo", "repo"),
        ("package", "package"),
        ("source", "source"),
    ):
        raw = meta.get(key)
        if raw:
            subject = str(raw).strip()
            subj_type = sanitize_label(hint or asset_type, default="other")
            break
    if not subject and host:
        subject, subj_type = host, asset_type or "host"
    if not subject and evidence_path:
        subject, subj_type = evidence_path, asset_type or "file"

    seq = getattr(finding, "seq", None)
    fid = f"finding:{getattr(finding, 'id', seq or 'x')}"
    title = (getattr(finding, "title", None) or "").strip()
    label_val = f"FIND-{seq}" if seq is not None else "FIND"
    if subject:
        short = _basename(subject) or subject
        label_val = f"{label_val} · {short[:28]}"

    finding_row = {
        "id": fid,
        "type": sanitize_label(getattr(finding, "kind", None) or "finding", default="finding")
        or "finding",
        "value": label_val[:256],
        "key": fid,
        "bucket": "finding",
        "source": "finding",
        "props": {
            "title": title[:200],
            "severity": getattr(finding, "severity", "") or "",
            "status": getattr(finding, "status", "") or "",
            "seq": seq,
            "kind": "finding",
        },
    }
    assets = [a for a in (graph.assets or []) if str(a.get("id") or "") != fid]
    assets.append(finding_row)
    graph.assets = assets

    subject_id = None
    if subject:
        row = upsert_asset(
            graph,
            typ=subj_type,
            value=subject,
            bucket="discovered",
            source="finding",
        )
        if row:
            subject_id = row["id"]
            upsert_relation(
                graph, rel="finding", source_id=subject_id, target_id=fid
            )

    port = getattr(finding, "port", None)
    if subject_id and port is not None and ":" not in subject and "/" not in subject:
        try:
            port_i = int(port)
        except (TypeError, ValueError):
            port_i = None
        if port_i is not None and 1 <= port_i <= 65535:
            svc = upsert_asset(
                graph,
                typ="service",
                value=f"{subject}:{port_i}",
                bucket="discovered",
                source="finding",
            )
            if svc:
                upsert_relation(
                    graph, rel="port", source_id=subject_id, target_id=svc["id"]
                )
                upsert_relation(
                    graph, rel="finding", source_id=svc["id"], target_id=fid
                )


def sync_project_graph(
    project: Project,
    *,
    findings: list | None = None,
    persist: bool = True,
) -> AssetGraph:
    """Rebuild/merge graph from RoE + findings (idempotent upserts)."""
    graph = get_or_create_graph(project)
    roe = getattr(project, "roe", None)
    sync_roe(graph, roe)
    for f in findings or []:
        sync_finding(graph, f)
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
            # Spoke edges from hub to other RoE assets
            if (
                a.get("id") != hub["id"]
                and a.get("bucket") in {"seed", "in_scope", "candidate", "exclusion", "discovered"}
            ):
                upsert_relation(
                    graph,
                    rel=str(a.get("bucket") or "related"),
                    source_id=str(hub["id"]),
                    target_id=str(a["id"]),
                )
    if persist:
        graph.save()
    return graph


def view_payload(graph: AssetGraph) -> dict[str, Any]:
    """UI payload: nodes/edges with open types; legend lists discovered kinds."""
    nodes: list[dict[str, Any]] = []
    types: set[str] = set()
    buckets: set[str] = set()
    rels: set[str] = set()

    for a in graph.assets or []:
        typ = str(a.get("type") or "other")
        value = str(a.get("value") or "")
        bucket = str(a.get("bucket") or "other")
        props = a.get("props") if isinstance(a.get("props"), dict) else {}
        kind = str(props.get("kind") or ("finding" if bucket == "finding" else "asset"))
        raw_source = str(a.get("source") or "")
        if raw_source in {"roe", "rules_of_engagement"}:
            display_source = "Rules of Engagement"
        else:
            display_source = raw_source
        types.add(typ)
        buckets.add(bucket)
        nodes.append(
            {
                "id": str(a.get("id") or ""),
                "label": value[:48] if kind != "finding" else value[:48],
                "type": typ,
                "bucket": bucket,
                "kind": kind,
                "center": bool(props.get("center") or kind == "hub"),
                "value": value,
                "source": display_source,
                "title": str(props.get("title") or ""),
                "severity": str(props.get("severity") or ""),
                "status": str(props.get("status") or ""),
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


def engagement_graph(
    project: Project,
    *,
    findings: list | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Sync + return dynamic graph payload for the attack-surface pane."""
    graph = sync_project_graph(project, findings=findings, persist=persist)
    return view_payload(graph)
