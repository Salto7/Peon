import type { Graph, WFNode } from "./types";

export const NODE_W = 200;
export const NODE_H = 72;
export const MIN_NODE_W = 148;
export const MIN_NODE_H = 56;
export const MAX_NODE_W = 420;
export const MAX_NODE_H = 280;
const GAP_X = 28;
const GAP_Y = 48;
const PAD = 28;

export function nodeSize(n: Pick<WFNode, "w" | "h">): { w: number; h: number } {
  const w = Number(n.w);
  const h = Number(n.h);
  return {
    w: Number.isFinite(w) && w > 0 ? Math.min(MAX_NODE_W, Math.max(MIN_NODE_W, Math.round(w))) : NODE_W,
    h: Number.isFinite(h) && h > 0 ? Math.min(MAX_NODE_H, Math.max(MIN_NODE_H, Math.round(h))) : NODE_H,
  };
}

function overlaps(
  ax: number,
  ay: number,
  aw: number,
  ah: number,
  bx: number,
  by: number,
  bw: number,
  bh: number,
): boolean {
  return ax < bx + bw && ax + aw > bx && ay < by + bh && ay + ah > by;
}

/** After a node grows, shove overlapping cards right or down so edges stay readable. */
export function reflowAfterResize(graph: Graph, id: string): Graph {
  const nodes = graph.nodes.map((n) => ({ ...n }));
  const self = nodes.find((n) => n.id === id);
  if (!self) return graph;
  const ss = nodeSize(self);
  let guard = 0;
  let moved = true;
  while (moved && guard++ < nodes.length * 4) {
    moved = false;
    for (const other of nodes) {
      if (other.id === id) continue;
      const os = nodeSize(other);
      if (!overlaps(self.x, self.y, ss.w, ss.h, other.x, other.y, os.w, os.h)) continue;
      const right = self.x + ss.w + GAP_X;
      const down = self.y + ss.h + GAP_Y;
      const dx = right - other.x;
      const dy = down - other.y;
      if (dx > 0 && (dx <= dy || dy <= 0)) {
        other.x = Math.round(right);
        moved = true;
      } else if (dy > 0) {
        other.y = Math.round(down);
        moved = true;
      }
    }
  }
  return { ...graph, nodes };
}

/** Layered top-down layout (depth = longest path from the root); returns a new graph with x / y set. */
export function autoLayout(graph: Graph): Graph {
  const incoming = new Map<string, string[]>();
  const outgoing = new Map<string, string[]>();
  for (const n of graph.nodes) {
    incoming.set(n.id, []);
    outgoing.set(n.id, []);
  }
  for (const e of graph.edges) {
    incoming.get(e.target)?.push(e.source);
    outgoing.get(e.source)?.push(e.target);
  }
  const depth = new Map<string, number>();
  const indeg = new Map<string, number>(graph.nodes.map((n) => [n.id, incoming.get(n.id)?.length ?? 0]));
  const queue = graph.nodes.filter((n) => (indeg.get(n.id) ?? 0) === 0).map((n) => n.id);
  while (queue.length) {
    const id = queue.shift() as string;
    const d = Math.max(0, ...(incoming.get(id) ?? []).map((p) => (depth.get(p) ?? 0) + 1));
    depth.set(id, d);
    for (const t of outgoing.get(id) ?? []) {
      indeg.set(t, (indeg.get(t) ?? 1) - 1);
      if ((indeg.get(t) ?? 0) === 0) queue.push(t);
    }
  }
  const layers = new Map<number, string[]>();
  for (const n of graph.nodes) {
    const d = depth.get(n.id) ?? 0;
    layers.set(d, [...(layers.get(d) ?? []), n.id]);
  }
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const xHint = new Map(graph.nodes.map((n) => [n.id, n.x]));
  const placed = new Map<string, { x: number; y: number }>();
  let y = PAD;
  const maxDepth = Math.max(0, ...layers.keys());
  for (let d = 0; d <= maxDepth; d++) {
    const ids = layers.get(d) ?? [];
    if (!ids.length) continue;
    ids.sort((a, b) => (xHint.get(a) ?? 0) - (xHint.get(b) ?? 0));
    let x = PAD;
    let rowH = NODE_H;
    for (const id of ids) {
      const size = nodeSize(byId.get(id) as WFNode);
      placed.set(id, { x: Math.round(x), y: Math.round(y) });
      x += size.w + GAP_X;
      rowH = Math.max(rowH, size.h);
    }
    y += rowH + GAP_Y;
  }
  return { ...graph, nodes: graph.nodes.map((n) => ({ ...n, ...(placed.get(n.id) ?? {}) })) };
}
