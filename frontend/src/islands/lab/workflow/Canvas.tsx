import { Maximize2, Minus, Plus, Wand2 } from "lucide-react";
import { type DragEvent, type PointerEvent as ReactPointerEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  MAX_NODE_H,
  MAX_NODE_W,
  MIN_NODE_H,
  MIN_NODE_W,
  NODE_H,
  NODE_W,
  nodeSize,
} from "./layout";
import {
  type CatalogOption,
  type Catalog,
  type Graph,
  type NodeKind,
  type WFNode,
  canLink,
} from "./types";

/**
 * The workflow canvas. Same look and feel as the war-room agent graph (dotted canvas, `.flow-node` cards, bezier
 * `.flow-edge` links) so a designed workflow reads like the agents it will become. Pan the background, wheel to
 * zoom, drag a card to move it, drag from a card's bottom handle onto another card to link them, drag the
 * bottom-right corner to resize (neighbors are shoved aside).
 */

export type Selection = { type: "node"; id: string } | { type: "edge"; id: string } | null;

type Props = {
  graph: Graph;
  catalog: Catalog | undefined;
  selection: Selection;
  problemNodes: ReadonlySet<string>;
  onSelect: (s: Selection) => void;
  onMove: (id: string, x: number, y: number) => void;
  onResize: (id: string, w: number, h: number) => void;
  onConnect: (source: string, target: string) => void;
  onDropCatalog: (kind: NodeKind, refId: string, x: number, y: number) => void;
  onDeleteSelection: () => void;
  onAutoLayout: () => void;
};

const GLYPH: Record<string, string> = { manager: "♛", analyzer: "⌕", specialist: "♞", skill: "♙", tool: "⚒" };
const LANE: Record<string, string> = { manager: "pm", analyzer: "analyzer", specialist: "expert", skill: "skill", tool: "skill" };

type View = { x: number; y: number; z: number };
type Drag =
  | { type: "pan"; sx: number; sy: number; vx: number; vy: number }
  | { type: "node"; id: string; sx: number; sy: number; nx: number; ny: number; moved: boolean }
  | { type: "resize"; id: string; sx: number; sy: number; nw: number; nh: number }
  | { type: "link"; source: string }
  | null;

export default function Canvas({
  graph,
  catalog,
  selection,
  problemNodes,
  onSelect,
  onMove,
  onResize,
  onConnect,
  onDropCatalog,
  onDeleteSelection,
  onAutoLayout,
}: Props) {
  const viewport = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag>(null);
  const [view, setView] = useState<View>({ x: 24, y: 24, z: 1 });
  const [live, setLive] = useState<Record<string, { x: number; y: number }>>({});
  const [liveSize, setLiveSize] = useState<Record<string, { w: number; h: number }>>({});
  const [pending, setPending] = useState<{ x: number; y: number } | null>(null);
  const [linkFrom, setLinkFrom] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);

  const meta = useMemo(() => {
    const m = new Map<string, CatalogOption & { role_kind?: string }>();
    for (const o of catalog?.roles ?? []) m.set(`role:${o.id}`, o);
    for (const o of catalog?.skills ?? []) m.set(`skill:${o.id}`, o);
    for (const o of catalog?.tools ?? []) m.set(`tool:${o.id}`, o);
    return m;
  }, [catalog]);

  const pos = (n: WFNode) => live[n.id] ?? { x: n.x, y: n.y };
  const size = (n: WFNode) => liveSize[n.id] ?? nodeSize(n);
  const byId = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph.nodes]);

  const toWorld = (clientX: number, clientY: number) => {
    const r = viewport.current?.getBoundingClientRect();
    return { x: (clientX - (r?.left ?? 0) - view.x) / view.z, y: (clientY - (r?.top ?? 0) - view.y) / view.z };
  };

  useEffect(() => {
    const el = viewport.current;
    if (!el) return;
    const onWheel = (ev: WheelEvent) => {
      ev.preventDefault();
      const r = el.getBoundingClientRect();
      setView((v) => {
        const z = Math.min(1.8, Math.max(0.35, v.z * (ev.deltaY < 0 ? 1.1 : 1 / 1.1)));
        const px = ev.clientX - r.left;
        const py = ev.clientY - r.top;
        return { z, x: px - ((px - v.x) / v.z) * z, y: py - ((py - v.y) / v.z) * z };
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const fit = () => {
    const el = viewport.current;
    if (!el || graph.nodes.length === 0) return setView({ x: 24, y: 24, z: 1 });
    const boxes = graph.nodes.map((n) => {
      const p = pos(n);
      const s = size(n);
      return { x0: p.x, y0: p.y, x1: p.x + s.w, y1: p.y + s.h };
    });
    const minX = Math.min(...boxes.map((b) => b.x0));
    const minY = Math.min(...boxes.map((b) => b.y0));
    const maxX = Math.max(...boxes.map((b) => b.x1));
    const maxY = Math.max(...boxes.map((b) => b.y1));
    const w = maxX - minX + 56;
    const h = maxY - minY + 56;
    const z = Math.min(1.1, Math.max(0.35, Math.min(el.clientWidth / w, el.clientHeight / h)));
    setView({ z, x: 28 - minX * z + (el.clientWidth - w * z) / 2, y: 28 - minY * z });
  };

  const first = useRef(true);
  useEffect(() => {
    if (first.current && graph.nodes.length) {
      first.current = false;
      fit();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- first load only
  }, [graph.nodes.length]);

  const startPan = (ev: ReactPointerEvent<HTMLDivElement>) => {
    if ((ev.target as HTMLElement).closest("[data-node-id],[data-edge-id],button")) return;
    drag.current = { type: "pan", sx: ev.clientX, sy: ev.clientY, vx: view.x, vy: view.y };
    viewport.current?.setPointerCapture(ev.pointerId);
    viewport.current?.classList.add("is-panning");
    onSelect(null);
  };

  const startNode = (ev: ReactPointerEvent<HTMLElement>, n: WFNode) => {
    if ((ev.target as HTMLElement).closest("[data-handle],[data-resize]")) return;
    ev.stopPropagation();
    const p = pos(n);
    drag.current = { type: "node", id: n.id, sx: ev.clientX, sy: ev.clientY, nx: p.x, ny: p.y, moved: false };
    viewport.current?.setPointerCapture(ev.pointerId);
    onSelect({ type: "node", id: n.id });
  };

  const startResize = (ev: ReactPointerEvent<HTMLElement>, n: WFNode) => {
    ev.stopPropagation();
    ev.preventDefault();
    const s = size(n);
    drag.current = { type: "resize", id: n.id, sx: ev.clientX, sy: ev.clientY, nw: s.w, nh: s.h };
    viewport.current?.setPointerCapture(ev.pointerId);
    onSelect({ type: "node", id: n.id });
  };

  const startLink = (ev: ReactPointerEvent<HTMLElement>, n: WFNode) => {
    ev.stopPropagation();
    ev.preventDefault();
    drag.current = { type: "link", source: n.id };
    setLinkFrom(n.id);
    setPending(toWorld(ev.clientX, ev.clientY));
    viewport.current?.setPointerCapture(ev.pointerId);
  };

  const onMovePointer = (ev: ReactPointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    if (d.type === "pan") {
      setView((v) => ({ ...v, x: d.vx + ev.clientX - d.sx, y: d.vy + ev.clientY - d.sy }));
    } else if (d.type === "node") {
      const dx = (ev.clientX - d.sx) / view.z;
      const dy = (ev.clientY - d.sy) / view.z;
      if (!d.moved && Math.hypot(dx, dy) < 3) return;
      d.moved = true;
      setLive((l) => ({ ...l, [d.id]: { x: Math.max(0, d.nx + dx), y: Math.max(0, d.ny + dy) } }));
    } else if (d.type === "resize") {
      const dw = (ev.clientX - d.sx) / view.z;
      const dh = (ev.clientY - d.sy) / view.z;
      setLiveSize((l) => ({
        ...l,
        [d.id]: {
          w: Math.min(MAX_NODE_W, Math.max(MIN_NODE_W, Math.round(d.nw + dw))),
          h: Math.min(MAX_NODE_H, Math.max(MIN_NODE_H, Math.round(d.nh + dh))),
        },
      }));
    } else if (d.type === "link") {
      setPending(toWorld(ev.clientX, ev.clientY));
      const under = document.elementFromPoint(ev.clientX, ev.clientY)?.closest("[data-node-id]");
      setHover(under ? (under as HTMLElement).dataset.nodeId ?? null : null);
    }
  };

  const endPointer = (ev: ReactPointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    drag.current = null;
    viewport.current?.classList.remove("is-panning");
    try {
      viewport.current?.releasePointerCapture(ev.pointerId);
    } catch {
      /* not captured */
    }
    if (!d) return;
    if (d.type === "node" && d.moved) {
      const p = live[d.id];
      if (p) onMove(d.id, Math.round(p.x), Math.round(p.y));
      setLive((l) => {
        const rest = { ...l };
        delete rest[d.id];
        return rest;
      });
    } else if (d.type === "resize") {
      const s = liveSize[d.id];
      if (s) onResize(d.id, s.w, s.h);
      setLiveSize((l) => {
        const rest = { ...l };
        delete rest[d.id];
        return rest;
      });
    } else if (d.type === "link") {
      const under = document.elementFromPoint(ev.clientX, ev.clientY)?.closest("[data-node-id]");
      const target = under ? (under as HTMLElement).dataset.nodeId : undefined;
      if (target && target !== d.source) onConnect(d.source, target);
      setPending(null);
      setLinkFrom(null);
      setHover(null);
    }
  };

  const onDragOver = (ev: DragEvent<HTMLDivElement>) => {
    if (ev.dataTransfer.types.includes("application/x-workflow-item")) {
      ev.preventDefault();
      ev.dataTransfer.dropEffect = "copy";
    }
  };
  const onDrop = (ev: DragEvent<HTMLDivElement>) => {
    const raw = ev.dataTransfer.getData("application/x-workflow-item");
    if (!raw) return;
    ev.preventDefault();
    try {
      const { kind, refId } = JSON.parse(raw) as { kind: NodeKind; refId: string };
      const w = toWorld(ev.clientX, ev.clientY);
      onDropCatalog(kind, refId, Math.round(w.x - NODE_W / 2), Math.round(w.y - NODE_H / 2));
    } catch {
      /* ignore a foreign drag */
    }
  };

  const anchorOut = (n: WFNode) => {
    const p = pos(n);
    const s = size(n);
    return { x: p.x + s.w / 2, y: p.y + s.h };
  };
  const anchorIn = (n: WFNode) => {
    const p = pos(n);
    const s = size(n);
    return { x: p.x + s.w / 2, y: p.y };
  };
  const curve = (a: { x: number; y: number }, b: { x: number; y: number }) => {
    const mid = a.y + (b.y - a.y) / 2;
    return `M ${a.x} ${a.y} C ${a.x} ${mid}, ${b.x} ${mid}, ${b.x} ${b.y}`;
  };

  const extent = useMemo(() => {
    const xs = graph.nodes.map((n) => pos(n).x + size(n).w);
    const ys = graph.nodes.map((n) => pos(n).y + size(n).h);
    return { w: Math.max(900, ...xs) + 80, h: Math.max(520, ...ys) + 80 };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- pos/size read live state
  }, [graph.nodes, live, liveSize]);

  return (
    // biome-ignore lint/a11y/noNoninteractiveTabindex: the canvas handles Delete for the selection
    <div className="agent-tree wf-canvas h-full min-h-[20rem]" tabIndex={0} onKeyDown={(e) => {
      if ((e.key === "Delete" || e.key === "Backspace") && !(e.target as HTMLElement).closest("input,textarea,select")) {
        e.preventDefault();
        onDeleteSelection();
      }
    }}>
      <div
        ref={viewport}
        className="org-agent-viewport"
        onPointerDown={startPan}
        onPointerMove={onMovePointer}
        onPointerUp={endPointer}
        onPointerCancel={endPointer}
        onDragOver={onDragOver}
        onDrop={onDrop}
      >
        <div className="org-agent-world" style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.z})` }}>
          <div className="flow-canvas" style={{ width: extent.w, height: extent.h }}>
            <svg className="flow-edges" width={extent.w} height={extent.h} aria-hidden="true">
              <defs>
                <marker id="wf-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path d="M 0 0 L 10 5 L 0 10 z" className="wf-arrow" />
                </marker>
              </defs>
              {graph.edges.map((e) => {
                const s = byId.get(e.source);
                const t = byId.get(e.target);
                if (!s || !t) return null;
                const d = curve(anchorOut(s), anchorIn(t));
                const sel = selection?.type === "edge" && selection.id === e.id;
                return (
                  <g key={e.id}>
                    <path
                      d={d}
                      className={`flow-edge wf-edge${e.kind === "parallel" ? " is-parallel" : ""}${sel ? " is-selected" : ""}`}
                      markerEnd="url(#wf-arrow)"
                    />
                    <path
                      d={d}
                      data-edge-id={e.id}
                      className="wf-edge-hit"
                      onPointerDown={(ev) => {
                        ev.stopPropagation();
                        onSelect({ type: "edge", id: e.id });
                      }}
                    />
                  </g>
                );
              })}
              {linkFrom && pending && byId.get(linkFrom) && (
                <path d={curve(anchorOut(byId.get(linkFrom) as WFNode), pending)} className="flow-edge wf-edge is-live" />
              )}
            </svg>
            {graph.nodes.map((n) => {
              const m = meta.get(`${n.kind}:${n.ref_id}`);
              const roleKind = n.kind === "role" ? (m?.role_kind ?? "specialist") : n.kind;
              const lane = LANE[roleKind] ?? "expert";
              const p = pos(n);
              const s = size(n);
              const selected = selection?.type === "node" && selection.id === n.id;
              const cfg = n.config ?? {};
              const subtitle =
                n.kind === "role"
                  ? (cfg.title as string) || (cfg.instruction as string) || m?.description || "no instruction yet"
                  : m?.description || n.kind;
              const attachedSkills = n.kind === "role" ? graph.edges.filter((e) => e.source === n.id && byId.get(e.target)?.kind === "skill").length : 0;
              const targetOk = linkFrom ? canLink(byId.get(linkFrom)?.kind as NodeKind, n.kind) && linkFrom !== n.id : false;
              return (
                // biome-ignore lint/a11y/useSemanticElements: matches the war-room node (a focusable card)
                <div
                  key={n.id}
                  role="button"
                  tabIndex={0}
                  data-node-id={n.id}
                  data-kind={n.kind}
                  className={`flow-node org-agent-node wf-node lane-${lane}${n.kind !== "role" ? " is-spawn" : ""}${
                    selected ? " is-selected" : ""
                  }${problemNodes.has(n.id) ? " has-problem" : ""}${linkFrom && hover === n.id ? (targetOk ? " is-link-ok" : " is-link-bad") : ""}`}
                  style={{ left: p.x, top: p.y, width: s.w, height: s.h }}
                  onPointerDown={(ev) => startNode(ev, n)}
                  onKeyDown={(ev) => {
                    if (ev.key === "Enter" || ev.key === " ") onSelect({ type: "node", id: n.id });
                  }}
                >
                  <div className="org-agent-row">
                    <div className="org-agent-icon" aria-hidden="true">
                      <span className="org-agent-glyph">{GLYPH[roleKind] ?? "♞"}</span>
                    </div>
                    <div className="org-agent-text">
                      <div className="flow-node-title org-agent-title" title={n.ref_id}>
                        {n.ref_id}
                      </div>
                      <div className="org-agent-skills">
                        <span className="org-agent-tag">{n.kind === "role" ? (m?.role_kind ?? "role") : n.kind}</span>
                        {attachedSkills > 0 && <span className="org-agent-tag org-agent-tag-skill">{attachedSkills} skills</span>}
                      </div>
                    </div>
                  </div>
                  <div className="wf-node-note">{subtitle}</div>
                  <span data-handle="in" className="wf-handle wf-handle-in" aria-hidden="true" />
                  <span
                    data-handle="out"
                    className="wf-handle wf-handle-out"
                    title="Drag onto another card to link"
                    onPointerDown={(ev) => startLink(ev, n)}
                  />
                  <span
                    data-resize
                    className="wf-resize"
                    title="Drag to resize"
                    aria-label="Resize card"
                    onPointerDown={(ev) => startResize(ev, n)}
                  />
                </div>
              );
            })}
          </div>
        </div>
      </div>
      {graph.nodes.length === 0 && (
        <p className="wf-empty">Pick a role card on the left (or drag it here) to start — the project manager first.</p>
      )}
      <div className="wf-zoom" role="group" aria-label="Canvas controls">
        <button type="button" title="Zoom in" aria-label="Zoom in" onClick={() => setView((v) => ({ ...v, z: Math.min(1.8, v.z * 1.15) }))}>
          <Plus className="size-4" />
        </button>
        <button type="button" title="Zoom out" aria-label="Zoom out" onClick={() => setView((v) => ({ ...v, z: Math.max(0.35, v.z / 1.15) }))}>
          <Minus className="size-4" />
        </button>
        <button type="button" title="Fit to view" aria-label="Fit to view" onClick={fit}>
          <Maximize2 className="size-4" />
        </button>
        <button
          type="button"
          title="Tidy up: lay the hierarchy out top-down"
          aria-label="Auto layout"
          onClick={() => {
            onAutoLayout();
            setTimeout(fit, 30);
          }}
        >
          <Wand2 className="size-4" />
        </button>
      </div>
    </div>
  );
}
