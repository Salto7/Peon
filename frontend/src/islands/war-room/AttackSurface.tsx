import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import fcose from "cytoscape-fcose";
import { List, Maximize2, Minimize2, Network, Scan } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { ToneBadge, humanize, statusTone, type Tone } from "@/components/status";
import { Button } from "@/components/ui/button";
import { useTheme } from "@/hooks/use-theme";
import { tokenColor } from "@/lib/colors";
import { cn } from "@/lib/utils";
import { useLiveProject, useOps } from "./live";
import type { Ops, ProjectContext, SurfaceEdge, SurfaceNode } from "./types";

cytoscape.use(fcose);

// Rules-of-Engagement bucket → fill colour token / badge tone.
const BUCKETS: Record<string, { token: string; tone: Tone; label: string }> = {
  in_scope: { token: "--success", tone: "success", label: "In scope" },
  seed: { token: "--primary", tone: "primary", label: "Seed" },
  candidate: { token: "--warning", tone: "warning", label: "Candidate" },
  exclusion: { token: "--destructive", tone: "danger", label: "Excluded" },
  discovered: { token: "--info", tone: "info", label: "Discovered" },
};
const bucketOf = (b: string) =>
  BUCKETS[b] ?? { token: "--muted-foreground", tone: "neutral" as Tone, label: humanize(b) };

// Asset group (Open Asset Model family) → node shape. Shape = what it is,
// fill = whether it is in scope, ring = security findings attached.
const GROUP_SHAPE: Record<string, cytoscape.Css.NodeShape> = {
  dns: "round-rectangle",
  network: "ellipse",
  service: "hexagon",
  web: "barrel",
  org: "octagon",
  identity: "diamond",
  registry: "tag",
  software: "pentagon",
  file: "rhomboid",
  location: "triangle",
  other: "ellipse",
};

type Legend = NonNullable<SurfaceNodeLegend>;
type SurfaceNodeLegend = {
  groups?: { slug: string; label: string; count: number }[];
  classes?: { slug: string; label: string; oam: string; group: string; count: number }[];
  relation_counts?: { label: string; title: string; count: number; status: string }[];
};

/**
 * Fit the viewport to the graph. cytoscape's `fit(undefined)` fits the
 * *viewport box*, not the elements, which pushes the graph off screen.
 */
function fitGraph(c: Core, animate = true) {
  const eles = c.elements();
  if (!eles.length) return;
  c.stop();
  if (animate) c.animate({ fit: { eles, padding: 40 } }, { duration: 450, easing: "ease-in-out-cubic" });
  else c.fit(eles, 40);
}

type Box = { cx: number; cy: number; l: number; r: number; t: number; b: number };

const hit = (p: Box, q: Box, pad: number) =>
  p.cx + p.l < q.cx + q.r + pad &&
  q.cx + q.l < p.cx + p.r + pad &&
  p.cy + p.t < q.cy + q.b + pad &&
  q.cy + q.t < p.cy + p.b + pad;

/**
 * Make node boxes (labels included) disjoint.
 *
 * Phase 1 relaxes all overlapping pairs at once and applies the *averaged*
 * displacement per node (no oscillation). Phase 2 deterministically moves any
 * node that is still touching to the nearest free spot on an expanding ring,
 * so the result is overlap-free regardless of density.
 */
export function removeOverlaps(cy: Core, padding = 10, maxIterations = 400): number {
  const nodes = cy.nodes(":visible").toArray();
  if (nodes.length < 2) return 0;
  const boxes: Box[] = nodes.map((n) => {
    const p = n.position();
    const bb = n.boundingBox({ includeLabels: true, includeOverlays: false });
    return { cx: p.x, cy: p.y, l: bb.x1 - p.x, r: bb.x2 - p.x, t: bb.y1 - p.y, b: bb.y2 - p.y };
  });
  const order = boxes.map((_, i) => i);
  let moves = 0;

  for (let iteration = 0; iteration < maxIterations; iteration += 1) {
    order.sort((i, j) => boxes[i]!.cx + boxes[i]!.l - (boxes[j]!.cx + boxes[j]!.l));
    const dx = new Float64Array(boxes.length);
    const dy = new Float64Array(boxes.length);
    const count = new Uint16Array(boxes.length);
    let touching = 0;
    for (let oi = 0; oi < order.length; oi += 1) {
      const i = order[oi]!;
      const a = boxes[i]!;
      for (let oj = oi + 1; oj < order.length; oj += 1) {
        const j = order[oj]!;
        const b = boxes[j]!;
        if (b.cx + b.l > a.cx + a.r + padding) break; // sorted by left edge
        if (!hit(a, b, padding)) continue;
        touching += 1;
        const ox = Math.min(a.cx + a.r, b.cx + b.r) - Math.max(a.cx + a.l, b.cx + b.l) + padding;
        const oy = Math.min(a.cy + a.b, b.cy + b.b) - Math.max(a.cy + a.t, b.cy + b.t) + padding;
        const ax = a.cx + (a.l + a.r) / 2;
        const bx = b.cx + (b.l + b.r) / 2;
        const ay = a.cy + (a.t + a.b) / 2;
        const by = b.cy + (b.t + b.b) / 2;
        if (ox < oy) {
          // coincident centres: split by index so the pair actually separates
          const s = ax === bx ? (i < j ? -1 : 1) : ax < bx ? -1 : 1;
          dx[i]! += (s * ox) / 2;
          dx[j]! -= (s * ox) / 2;
        } else {
          const s = ay === by ? (i < j ? -1 : 1) : ay < by ? -1 : 1;
          dy[i]! += (s * oy) / 2;
          dy[j]! -= (s * oy) / 2;
        }
        count[i]! += 1;
        count[j]! += 1;
      }
    }
    if (!touching) break;
    for (let k = 0; k < boxes.length; k += 1) {
      const c = count[k]!;
      if (!c) continue;
      boxes[k]!.cx += (dx[k]! / c) * 1.15;
      boxes[k]!.cy += (dy[k]! / c) * 1.15;
      moves += 1;
    }
  }

  // Phase 2: anything still touching goes to the nearest free ring slot.
  const stuck = new Set<number>();
  for (let i = 0; i < boxes.length; i += 1) {
    for (let j = i + 1; j < boxes.length; j += 1) {
      if (hit(boxes[i]!, boxes[j]!, padding)) stuck.add(j);
    }
  }
  for (const k of stuck) {
    const me = boxes[k]!;
    const w = me.r - me.l + padding;
    const h = me.b - me.t + padding;
    const home = { x: me.cx, y: me.cy };
    let placed = false;
    for (let ring = 1; ring <= 60 && !placed; ring += 1) {
      const steps = Math.max(8, ring * 8);
      for (let s = 0; s < steps && !placed; s += 1) {
        const angle = (s / steps) * Math.PI * 2;
        me.cx = home.x + Math.cos(angle) * ring * w * 0.6;
        me.cy = home.y + Math.sin(angle) * ring * h * 0.6;
        placed = !boxes.some((o, idx) => idx !== k && hit(me, o, padding));
      }
    }
    if (!placed) {
      me.cx = home.x;
      me.cy = home.y;
    }
    moves += 1;
  }

  cy.batch(() => {
    nodes.forEach((n, i) => {
      n.position({ x: boxes[i]!.cx, y: boxes[i]!.cy });
    });
  });
  return moves;
}

/** Count node pairs whose boxes (labels included) still intersect. */
export function countOverlaps(cy: Core): number {
  const boxes = cy.nodes(":visible").map((n) => n.boundingBox({ includeLabels: true }));
  let hits = 0;
  for (let i = 0; i < boxes.length; i += 1) {
    for (let j = i + 1; j < boxes.length; j += 1) {
      const a = boxes[i]!;
      const b = boxes[j]!;
      if (a.x1 < b.x2 && b.x1 < a.x2 && a.y1 < b.y2 && b.y1 < a.y2) hits += 1;
    }
  }
  return hits;
}

export default function AttackSurface({ ctx, ops: initial }: { ctx: ProjectContext; ops: Ops }) {
  useLiveProject(ctx.pk);
  const { data: ops } = useOps(ctx.pk, initial);
  const surface = ops?.attack_surface;
  const theme = useTheme();
  const host = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);
  const [selected, setSelected] = useState<SurfaceNode | null>(null);
  const [view, setView] = useState<"graph" | "list">("graph");
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setExpanded(false);
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [expanded]);

  const nodes = useMemo(() => surface?.nodes ?? [], [surface]);
  const edges = useMemo(() => surface?.edges ?? [], [surface]);
  const legend = (surface?.legend ?? {}) as Legend;
  const signature = useMemo(
    () =>
      nodes.map((n) => `${n.id}:${n.bucket}:${n.finding_count}:${n.degree ?? 0}`).join("|") +
      edges.map((e) => `${e.source}>${e.target}:${e.rel}`).join("|"),
    [nodes, edges],
  );

  useEffect(() => {
    if (!host.current || view !== "graph" || !surface) return;
    const ids = new Set(surface.nodes.map((n) => n.id));
    const elements: ElementDefinition[] = [
      ...surface.nodes.map((n) => ({
        data: {
          id: n.id,
          label: n.label.length > 36 ? `${n.label.slice(0, 34)}…` : n.label,
          color: tokenColor(bucketOf(n.bucket).token),
          shape: GROUP_SHAPE[n.group ?? "other"] ?? "ellipse",
          size: n.center ? 40 : 20 + Math.min(18, (n.degree ?? 0) * 3),
          findings: n.finding_count ?? 0,
          group: n.group ?? "other",
        },
      })),
      ...surface.edges
        .filter((e) => ids.has(e.source) && ids.has(e.target))
        .map((e, i) => ({
          data: {
            id: `e${i}`,
            source: e.source,
            target: e.target,
            rel: e.title || e.rel,
            status: e.status ?? "ok",
            derived: e.derived ? 1 : 0,
          },
        })),
    ];
    const fg = tokenColor("--foreground");
    const muted = tokenColor("--muted-foreground");
    const line = tokenColor("--input");
    const ring = tokenColor("--ring");
    const bg = tokenColor("--canvas");
    const danger = tokenColor("--destructive");
    // cytoscape mutates its container, so give it a private child: React
    // never sees (and never trips over) the nodes cytoscape injects.
    const mount = document.createElement("div");
    mount.style.cssText = "position:absolute;inset:0";
    host.current.appendChild(mount);
    const instance = cytoscape({
      container: mount,
      elements,
      minZoom: 0.15,
      maxZoom: 2.5,
      style: [
        {
          selector: "node",
          style: {
            "background-color": "data(color)",
            shape: "data(shape)" as unknown as cytoscape.Css.NodeShape,
            width: "data(size)",
            height: "data(size)",
            label: "data(label)",
            color: fg,
            "font-size": 10,
            "font-family": "Geist Variable, ui-sans-serif, sans-serif",
            "text-valign": "bottom",
            "text-margin-y": 5,
            "text-wrap": "ellipsis",
            "text-max-width": "130px",
            "text-background-color": bg,
            "text-background-opacity": 0.85,
            "text-background-padding": "2px",
            "border-width": 2,
            "border-color": bg,
          },
        },
        { selector: "node[findings > 0]", style: { "border-color": danger, "border-width": 3 } },
        { selector: "node:selected", style: { "border-color": ring, "border-width": 4 } },
        { selector: "node.dim", style: { opacity: 0.15 } },
        { selector: "node.faded", style: { opacity: 0.2 } },
        { selector: "edge.faded", style: { opacity: 0.06 } },
        { selector: "edge.lit", style: { width: 2.25, "line-color": ring, "target-arrow-color": ring, "line-style": "dashed", "line-dash-pattern": [6, 4] } },
        {
          selector: "node, edge",
          style: { "transition-property": "opacity", "transition-duration": 180 } as cytoscape.Css.Node,
        },
        {
          selector: "edge",
          style: {
            width: 1.25,
            "line-color": line,
            "target-arrow-color": line,
            "target-arrow-shape": "triangle",
            "arrow-scale": 0.7,
            "curve-style": "bezier",
            label: "data(rel)",
            "font-size": 8,
            color: muted,
            "text-rotation": "autorotate",
            "text-background-color": bg,
            "text-background-opacity": 0.8,
            "text-background-padding": "1px",
          },
        },
        { selector: "edge[derived = 1]", style: { width: 1, opacity: 0.8 } },
        { selector: "edge[status = 'unexpected']", style: { "line-style": "dashed", "line-color": tokenColor("--warning") } },
        { selector: "edge[status = 'custom']", style: { "line-style": "dotted" } },
        { selector: "edge.dim", style: { opacity: 0.08 } },
      ],
      layout: {
        name: "fcose",
        animate: false,
        quality: "proof",
        randomize: true,
        nodeDimensionsIncludeLabels: true,
        uniformNodeDimensions: false,
        packComponents: true,
        nodeRepulsion: () => 14000,
        idealEdgeLength: () => 110,
        edgeElasticity: () => 0.35,
        nodeSeparation: 90,
        padding: 40,
      } as cytoscape.LayoutOptions,
    });
    // fcose resolves most overlap; this pass guarantees the rest (labels included).
    removeOverlaps(instance);
    fitGraph(instance, false);

    // Entrance: nodes fade/scale in, edges draw in after them.
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (!reduce) {
      instance.nodes().forEach((n) => {
        const size = Number(n.data("size"));
        n.style({ opacity: 0, width: size * 0.3, height: size * 0.3 });
        n.animate(
          { style: { opacity: 1, width: size, height: size } },
          { duration: 450, easing: "ease-out-cubic", queue: false },
        );
      });
      instance.edges().style("opacity", 0);
      instance.edges().animate({ style: { opacity: 1 } }, { duration: 600, easing: "ease-in-out" });
    }

    // Hover: spotlight the neighbourhood.
    instance.on("mouseover", "node", (e) => {
      const hood = e.target.closedNeighborhood();
      instance.batch(() => {
        instance.elements().not(hood).addClass("faded");
        hood.addClass("lit");
      });
    });
    instance.on("mouseout", "node", () => {
      instance.batch(() => instance.elements().removeClass("faded").removeClass("lit"));
    });

    // Heartbeat: nodes with findings breathe; edges of selected node march.
    let raf = 0;
    let dash = 0;
    const flagged = instance.nodes("[findings > 0]");
    const t0 = performance.now();
    const tick = (now: number) => {
      if (!reduce) {
        const k = (Math.sin((now - t0) / 450) + 1) / 2;
        flagged.style({ "border-width": 3 + k * 3, "border-opacity": 0.55 + k * 0.45 });
        dash = (dash + 0.4) % 1000;
        instance.edges(".lit").style("line-dash-offset", -dash);
      }
      raf = requestAnimationFrame(tick);
    };
    if (!reduce) raf = requestAnimationFrame(tick);
    instance.on("tap", "node", (e) =>
      setSelected(surface.nodes.find((n) => n.id === e.target.id()) ?? null),
    );
    instance.on("tap", (e) => e.target === instance && setSelected(null));
    cy.current = instance;
    (host.current as HTMLDivElement & { __cy?: Core }).__cy = instance;

    const ro = new ResizeObserver(() => {
      instance.resize();
      fitGraph(instance, false);
    });
    ro.observe(host.current);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      try {
        instance.destroy();
      } catch {
        /* container already detached */
      }
      mount.remove();
      cy.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- rebuild only when the graph or theme changes
  }, [signature, theme, view]);

  // Group filter dims rather than relayouts, so positions stay stable.
  useEffect(() => {
    const c = cy.current;
    if (!c) return;
    c.batch(() => {
      c.nodes().forEach((n) => {
        n.toggleClass("dim", hidden.has(String(n.data("group"))));
      });
      c.edges().forEach((e) => {
        e.toggleClass("dim", e.source().hasClass("dim") || e.target().hasClass("dim"));
      });
    });
  }, [hidden, signature, view]);

  const neighbours = useMemo(() => {
    if (!selected) return [] as { other: SurfaceNode; edge: SurfaceEdge; dir: "out" | "in" }[];
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const out: { other: SurfaceNode; edge: SurfaceEdge; dir: "out" | "in" }[] = [];
    for (const e of edges) {
      if (e.source === selected.id && byId.has(e.target)) out.push({ other: byId.get(e.target)!, edge: e, dir: "out" });
      else if (e.target === selected.id && byId.has(e.source)) out.push({ other: byId.get(e.source)!, edge: e, dir: "in" });
    }
    return out;
  }, [selected, nodes, edges]);

  if (!nodes.length) {
    return (
      <div className="rounded-xl border border-dashed p-10 text-center text-sm text-muted-foreground">
        No assets yet — the graph fills in as agents and the Rules of Engagement add hosts, services, people and files.
      </div>
    );
  }

  const toggleGroup = (slug: string) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });

  const visibleNodes = nodes.filter((n) => !hidden.has(n.group ?? "other"));

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_20rem]">
      <section
        className={cn(
          "overflow-hidden border bg-card shadow-sm",
          expanded ? "fixed inset-0 z-50 flex flex-col rounded-none" : "rounded-xl",
        )}
        aria-label="Asset graph"
      >
        <div className="flex flex-wrap items-center gap-1.5 border-b px-4 py-2.5">
          {(legend.groups ?? []).map((g) => {
            const off = hidden.has(g.slug);
            return (
              <button
                key={g.slug}
                type="button"
                aria-pressed={!off}
                onClick={() => toggleGroup(g.slug)}
                className={cn(
                  "inline-flex h-7 cursor-pointer items-center gap-1.5 rounded-md border px-2 text-xs font-medium transition-colors",
                  off ? "text-muted-foreground line-through opacity-60" : "bg-muted hover:bg-accent",
                )}
              >
                {g.label}
                <span className="tabular-nums text-muted-foreground">{g.count}</span>
              </button>
            );
          })}
          <div className="ml-auto flex gap-1">
            {view === "graph" && (
              <>
                <Button variant="ghost" size="icon" className="size-8" aria-label="Fit graph" title="Fit graph" onClick={() => cy.current && fitGraph(cy.current)}>
                  <Scan />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-8"
                  aria-label={expanded ? "Exit full screen" : "Maximise graph"}
                  title={expanded ? "Exit full screen (Esc)" : "Maximise graph"}
                  aria-pressed={expanded}
                  onClick={() => setExpanded((v) => !v)}
                >
                  {expanded ? <Minimize2 /> : <Maximize2 />}
                </Button>
              </>
            )}
            <Button
              variant="ghost"
              size="icon"
              className="size-8"
              aria-label={view === "graph" ? "Show as list" : "Show as graph"}
              onClick={() => setView(view === "graph" ? "list" : "graph")}
            >
              {view === "graph" ? <List /> : <Network />}
            </Button>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b px-4 py-2 text-[11px] text-muted-foreground">
          {Object.entries(BUCKETS).map(([k, v]) => (
            <span key={k} className="inline-flex items-center gap-1.5">
              <span
                className="size-2.5 rounded-full"
                style={{ background: `var(${v.token})` }}
                aria-hidden
              />
              {v.label}
            </span>
          ))}
          <span className="inline-flex items-center gap-1.5">
            <span className="size-2.5 rounded-full border-2 border-destructive" aria-hidden />
            Has findings
          </span>
        </div>
        {view === "graph" ? (
          <div
            ref={host}
            data-attack-graph
            className={cn("relative bg-canvas", expanded ? "min-h-0 flex-1" : "h-[min(70vh,44rem)]")}
          />
        ) : (
          <div className="ui-table-wrap max-h-[70vh] overflow-y-auto">
            <table className="ui-table">
              <thead>
                <tr>
                  <th>Asset</th>
                  <th>Class</th>
                  <th>Scope</th>
                  <th className="text-right">Links</th>
                  <th className="text-right">Findings</th>
                </tr>
              </thead>
              <tbody>
                {visibleNodes.map((n) => (
                  <tr
                    key={n.id}
                    className={cn("cursor-pointer", selected?.id === n.id && "bg-muted/60")}
                    onClick={() => setSelected(n)}
                  >
                    <td className="w-full max-w-0">
                      <span className="block truncate font-mono text-xs">{n.value || n.label}</span>
                    </td>
                    <td className="whitespace-nowrap">{n.class_label ?? humanize(n.type)}</td>
                    <td>
                      <ToneBadge tone={bucketOf(n.bucket).tone}>{bucketOf(n.bucket).label}</ToneBadge>
                    </td>
                    <td className="text-right tabular-nums">{n.degree ?? 0}</td>
                    <td className="text-right tabular-nums">{n.finding_count ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {!!legend.relation_counts?.length && (
          <div className="flex flex-wrap gap-1.5 border-t px-4 py-2.5">
            {legend.relation_counts.map((r) => (
              <ToneBadge key={r.label} tone={r.status === "unexpected" ? "warning" : "neutral"}>
                {r.title} · {r.count}
              </ToneBadge>
            ))}
          </div>
        )}
      </section>

      <aside className="rounded-xl border bg-card p-4 shadow-sm xl:self-start" aria-live="polite">
        {selected ? (
          <div className="space-y-4 text-sm">
            <div className="space-y-1">
              <p className="text-xs text-muted-foreground">
                {selected.class_label ?? humanize(selected.type)}
                {selected.oam ? ` · OAM ${selected.oam}` : ""}
              </p>
              <h3 className="font-mono text-[13px] font-medium break-all">{selected.value || selected.label}</h3>
            </div>
            <div className="flex flex-wrap gap-1.5">
              <ToneBadge tone={bucketOf(selected.bucket).tone}>{bucketOf(selected.bucket).label}</ToneBadge>
              {selected.severity && <ToneBadge tone={statusTone(selected.severity)}>{humanize(selected.severity)}</ToneBadge>}
              {!!selected.finding_count && <ToneBadge tone="danger">{selected.finding_count} findings</ToneBadge>}
            </div>

            {neighbours.length > 0 && (
              <section className="space-y-1.5">
                <h4 className="text-xs font-medium text-muted-foreground">Relations</h4>
                <ul className="space-y-1">
                  {neighbours.slice(0, 40).map(({ other, edge, dir }) => (
                    <li key={`${edge.source}>${edge.target}:${edge.rel}`}>
                      <button
                        type="button"
                        onClick={() => setSelected(other)}
                        className="flex w-full cursor-pointer items-baseline gap-2 rounded-md px-1.5 py-1 text-left text-xs hover:bg-accent"
                      >
                        <span className="shrink-0 text-muted-foreground">
                          {dir === "out" ? "→" : "←"} {edge.title ?? edge.rel}
                        </span>
                        <span className="truncate font-mono">{other.value || other.label}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {!!selected.sources?.length && (
              <section className="space-y-1.5">
                <h4 className="text-xs font-medium text-muted-foreground">Observed by</h4>
                <ul className="space-y-1 text-xs">
                  {selected.sources.map((s) => (
                    <li key={s.name} className="flex items-baseline justify-between gap-2">
                      <span className="truncate">{s.name === "rules_of_engagement" ? "Rules of Engagement" : s.name === "finding" ? "Finding subject" : s.name}</span>
                      <span className="shrink-0 tabular-nums text-muted-foreground" title={`first ${s.first_seen}`}>
                        {s.last_seen ? new Date(s.last_seen).toLocaleString() : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            <dl className="space-y-2 text-xs">
              {[
                ["Type slug", selected.type],
                ["First seen", selected.first_seen && new Date(selected.first_seen).toLocaleString()],
                ["Title", selected.title],
                ["Status", selected.status && humanize(selected.status)],
              ]
                .filter(([, v]) => v)
                .map(([k, v]) => (
                  <div key={k}>
                    <dt className="text-muted-foreground">{k}</dt>
                    <dd className="mt-0.5 break-all">{v}</dd>
                  </div>
                ))}
            </dl>
          </div>
        ) : (
          <div className="space-y-3 text-sm">
            <p className="text-muted-foreground">Select an asset to see its relations and provenance.</p>
            <dl className="grid grid-cols-2 gap-3 text-xs">
              <div>
                <dt className="text-muted-foreground">Assets</dt>
                <dd className="mt-0.5 text-base font-semibold tabular-nums">{nodes.length}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Relations</dt>
                <dd className="mt-0.5 text-base font-semibold tabular-nums">{edges.length}</dd>
              </div>
            </dl>
            {!!legend.classes?.length && (
              <ul className="space-y-1 text-xs">
                {legend.classes.slice(0, 12).map((c) => (
                  <li key={c.slug} className="flex justify-between gap-2">
                    <span className="truncate">{c.label}</span>
                    <span className="tabular-nums text-muted-foreground">{c.count}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </aside>
    </div>
  );
}
