"""The designer graph as data: parsing, hierarchy semantics, auto-layout.

A workflow is a *hierarchy that executes*:

* ``role → role``  — hand-off. ``sequence``: the target starts when the source has finished. ``parallel``: the
  target runs beside the source (it waits for whatever the source waits for, not for the source itself).
* ``role → skill`` — the skill is part of that role's toolbox for this step (becomes the objective's ``skill_ids``).
* ``role → tool`` / ``skill → tool`` — a catalog CLI the step needs (provisioned before use).

There is exactly one root, a role (normally the project manager). Skills belong to exactly one role.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

NODE_KINDS = frozenset({"role", "skill", "tool"})
EDGE_KINDS = frozenset({"sequence", "parallel"})
#: (source kind, target kind) pairs an edge may connect.
ALLOWED_LINKS = frozenset(
    {("role", "role"), ("role", "skill"), ("role", "tool"), ("skill", "tool")}
)

SEED_PLACEHOLDER = r"\{\{\s*([A-Za-z0-9_\-]+)\s*\}\}"


@dataclass
class Graph:
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: list[dict[str, Any]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # structural problems found while parsing
    outgoing: dict[str, list[str]] = field(default_factory=dict)
    incoming: dict[str, list[str]] = field(default_factory=dict)

    def kind(self, node_id: str) -> str:
        return str(self.nodes[node_id].get("kind") or "")

    def ref(self, node_id: str) -> str:
        return str(self.nodes[node_id].get("ref_id") or "").strip()

    def children(self, node_id: str, kind: str | None = None) -> list[str]:
        return [t for t in self.outgoing.get(node_id, []) if kind is None or self.kind(t) == kind]

    def roots(self) -> list[str]:
        return [n for n in self.nodes if not self.incoming.get(n)]


def parse_graph(raw: Any) -> Graph:
    """Tolerant parse of ``{"nodes": [...], "edges": [...]}``; what is wrong is listed in ``problems``."""
    g = Graph()
    nodes = raw.get("nodes") if isinstance(raw, dict) else None
    edges = raw.get("edges") if isinstance(raw, dict) else None
    for node in nodes if isinstance(nodes, list) else []:
        node = node if isinstance(node, dict) else {}
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            g.problems.append("a node is missing an id")
            continue
        if node_id in g.nodes:
            g.problems.append(f"duplicate node id {node_id!r}")
            continue
        g.nodes[node_id] = node
    g.outgoing = {n: [] for n in g.nodes}
    g.incoming = {n: [] for n in g.nodes}
    seen: set[tuple[str, str]] = set()
    for edge in edges if isinstance(edges, list) else []:
        edge = edge if isinstance(edge, dict) else {}
        source, target = (
            str(edge.get("source") or "").strip(),
            str(edge.get("target") or "").strip(),
        )
        if source not in g.nodes or target not in g.nodes:
            g.problems.append(f"edge {source!r} -> {target!r} names an unknown node")
            continue
        if source == target:
            g.problems.append(f"edge {source!r} -> {target!r} connects a node to itself")
            continue
        if (source, target) in seen:
            g.problems.append(f"duplicate edge {source!r} -> {target!r}")
            continue
        seen.add((source, target))
        g.edges.append(
            {
                **edge,
                "source": source,
                "target": target,
                "kind": str(edge.get("kind") or "sequence").strip() or "sequence",
            }
        )
        g.outgoing[source].append(target)
        g.incoming[target].append(source)
    return g


def find_cycle(g: Graph) -> list[str] | None:
    color = dict.fromkeys(g.nodes, 0)  # 0 new, 1 on stack, 2 done
    path: list[str] = []

    def visit(n: str) -> list[str] | None:
        color[n] = 1
        path.append(n)
        for t in g.outgoing.get(n, ()):
            if color[t] == 1:
                return path[path.index(t) :] + [t]
            if color[t] == 0 and (found := visit(t)):
                return found
        path.pop()
        color[n] = 2
        return None

    for n in g.nodes:
        if color[n] == 0 and (found := visit(n)):
            return found
    return None


def topo_order(g: Graph, kind: str | None = None) -> list[str]:
    """Nodes in dependency order (Kahn), ties broken by canvas position (top→bottom, left→right)."""
    indeg = {n: len(g.incoming.get(n, [])) for n in g.nodes}
    pos = lambda n: (float(g.nodes[n].get("y") or 0), float(g.nodes[n].get("x") or 0), n)  # noqa: E731
    ready = sorted([n for n, d in indeg.items() if d == 0], key=pos)
    out: list[str] = []
    while ready:
        n = ready.pop(0)
        out.append(n)
        for t in g.outgoing.get(n, ()):
            indeg[t] -= 1
            if indeg[t] == 0:
                ready.append(t)
        ready.sort(key=pos)
    return [n for n in out if kind is None or g.kind(n) == kind]


def _node_size(node: dict[str, Any], *, default_w: int, default_h: int) -> tuple[int, int]:
    try:
        w = int(node.get("w") or 0)
    except (TypeError, ValueError):
        w = 0
    try:
        h = int(node.get("h") or 0)
    except (TypeError, ValueError):
        h = 0
    return (w if w > 0 else default_w, h if h > 0 else default_h)


def auto_layout(
    graph: dict[str, Any], *, node_w: int = 200, node_h: int = 72, gap_x: int = 28, gap_y: int = 48
) -> dict[str, Any]:
    """Layered top-down layout (depth = longest path from the root): returns a copy with x / y set.

    Per-node ``w`` / ``h`` (when present) are kept and used for spacing so resized
    cards stay consistent after a tidy-up.
    """
    g = parse_graph(graph)
    depth: dict[str, int] = {}
    for n in topo_order(g):
        depth[n] = max([depth.get(p, 0) + 1 for p in g.incoming.get(n, [])] or [0])
    layers: dict[int, list[str]] = {}
    for n in g.nodes:
        layers.setdefault(depth.get(n, 0), []).append(n)
    placed: dict[str, tuple[int, int]] = {}
    y = 28
    for d in sorted(layers):
        members = layers[d]
        members.sort(key=lambda n: (float(g.nodes[n].get("x") or 0), n))
        x = 28
        row_h = node_h
        for n in members:
            w, h = _node_size(g.nodes[n], default_w=node_w, default_h=node_h)
            placed[n] = (x, y)
            x += w + gap_x
            row_h = max(row_h, h)
        y += row_h + gap_y
    out = dict(graph)
    out["nodes"] = [
        {
            **node,
            "x": placed.get(str(node.get("id")), (node.get("x", 0), node.get("y", 0)))[0],
            "y": placed.get(str(node.get("id")), (node.get("x", 0), node.get("y", 0)))[1],
        }
        for node in (graph.get("nodes") or [])
    ]
    return out
