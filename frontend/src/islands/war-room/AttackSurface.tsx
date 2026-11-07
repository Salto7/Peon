import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import fcose from "cytoscape-fcose";
import {
  Check,
  Copy,
  EyeOff,
  List,
  Maximize2,
  Minimize2,
  Network,
  Play,
  Power,
  RefreshCw,
  Scan,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ToneBadge, humanize, statusTone, type Tone } from "@/components/status";
import { Button } from "@/components/ui/button";
import { useTheme } from "@/hooks/use-theme";
import { tokenColor } from "@/lib/colors";
import { cn } from "@/lib/utils";
import { useLiveProject, useOps } from "./live";
import type { Ops, ProjectContext, SurfaceEdge, SurfaceNode } from "./types";

cytoscape.use(fcose);

// RoE bucket → border colour (scope status) + badge tone.
const BUCKETS: Record<string, { token: string; tone: Tone; label: string }> = {
  in_scope: { token: "--success", tone: "success", label: "In scope" },
  seed: { token: "--primary", tone: "primary", label: "Seed" },
  candidate: { token: "--warning", tone: "warning", label: "Candidate" },
  exclusion: { token: "--muted-foreground", tone: "neutral", label: "Excluded" },
  discovered: { token: "--info", tone: "info", label: "Discovered" },
};
const bucketOf = (b: string) =>
  BUCKETS[b] ?? { token: "--muted-foreground", tone: "neutral" as Tone, label: humanize(b) };

// Circles only: fill ≈ Amass OAM viz colours (hex — Cytoscape rejects CSS Space HSL).
// Borders are white; only vulnerability nodes use a red border.

/** Amass `WriteD3Data` colours, as hex. IP/service remapped off Amass red/deeppink. */
const AMASS_TYPE_COLORS: Record<string, string> = {
  account: "#FFD700",
  as: "#4169E1",
  asn: "#4169E1",
  autonomoussystem: "#4169E1",
  autnum: "#E8DCC8",
  autnumrecord: "#E8DCC8",
  contact: "#FFF8DC",
  contactrecord: "#FFF8DC",
  domain: "#32CD32",
  domainrecord: "#87CEEB",
  file: "#FFD700",
  fqdn: "#32CD32",
  host: "#6495ED",
  funds: "#D2691E",
  fundstransfer: "#D2691E",
  hash: "#FFA500",
  identifier: "#FFA500",
  ip: "#6495ED",
  ipaddress: "#6495ED",
  ipv4: "#6495ED",
  ipv6: "#6495ED",
  ipnetrecord: "#A9A9A9",
  location: "#FFD700",
  netblock: "#FFB6C1",
  cidr: "#FFB6C1",
  organization: "#40E0D0",
  organisation: "#40E0D0",
  org: "#40E0D0",
  asset: "#40E0D0",
  person: "#FFE4C4",
  phone: "#FF7F50",
  product: "#2F4F4F",
  productrelease: "#2F4F4F",
  // Amass service=deeppink (reddish) → orchid.
  service: "#DA70D6",
  port: "#DA70D6",
  tls: "#87CEEB",
  tlscertificate: "#87CEEB",
  cert: "#87CEEB",
  url: "#2F4F4F",
  uri: "#2F4F4F",
  repo: "#2F4F4F",
  path: "#FFD700",
  function: "#20B2AA",
  // Vuln fills stay warm amber; border (below) is the red signal.
  vuln: "#F59E0B",
  vulnerability: "#F59E0B",
  cve: "#F59E0B",
  finding: "#F59E0B",
  exploit: "#F59E0B",
  other: "#708090",
};

const NODE_BORDER = "#FFFFFF";
/** Red border only for vulnerability-typed nodes. */
const VULN_BORDER = "#EF4444";

const NON_RED_FALLBACK = [
  "#4169E1",
  "#32CD32",
  "#FFA500",
  "#40E0D0",
  "#DA70D6",
  "#FFD700",
  "#6495ED",
  "#20B2AA",
  "#D2691E",
  "#2F4F4F",
  "#FF7F50",
  "#87CEEB",
] as const;

function isVulnType(typ: string): boolean {
  return /^(vuln|vulnerability|cve|finding|exploit)s?$/.test((typ || "").toLowerCase());
}

function nodeBorderColor(typ: string): string {
  return isVulnType(typ) ? VULN_BORDER : NODE_BORDER;
}

function colorForType(typ: string, fallbackIndex = 0): string {
  const s = (typ || "other").toLowerCase();
  const known = AMASS_TYPE_COLORS[s];
  if (known) return known;
  if (isVulnType(s)) return "#F59E0B";
  return NON_RED_FALLBACK[fallbackIndex % NON_RED_FALLBACK.length] ?? "#708090";
}

function buildTypeColors(types: Iterable<string>): Map<string, string> {
  const unique = [...new Set([...types].map((t) => (t || "other").toLowerCase()))].sort();
  const out = new Map<string, string>();
  let fallback = 0;
  for (const t of unique) {
    if (AMASS_TYPE_COLORS[t] || isVulnType(t)) {
      out.set(t, colorForType(t));
    } else {
      out.set(t, colorForType(t, fallback++));
    }
  }
  return out;
}

function TypeDot({
  className,
  borderToken,
  borderColor,
  color,
}: {
  className?: string;
  borderToken?: string;
  /** Explicit border hex (wins over borderToken). */
  borderColor?: string;
  color?: string;
}) {
  const fill = color || AMASS_TYPE_COLORS.other;
  const border = borderColor || tokenColor(borderToken || "--border");
  return (
    <span
      className={cn("inline-block size-2.5 shrink-0 rounded-full", className)}
      style={{ backgroundColor: fill, boxShadow: `inset 0 0 0 1.5px ${border}` }}
      aria-hidden
    />
  );
}

type Legend = {
  kinds?: { slug: string; label: string; count: number }[];
  relation_counts?: { label: string; title: string; count: number; status: string }[];
};

type Neighbour = { other: SurfaceNode; edge: SurfaceEdge; dir: "out" | "in" };

/**
 * Fit the viewport to the visible graph. cytoscape's `fit(undefined)` fits the
 * *viewport box*, not the elements, which pushes the graph off screen.
 */
function fitGraph(c: Core, animate = true) {
  const eles = c.elements(":visible");
  if (!eles.length) return;
  c.stop();
  if (animate)
    c.animate({ fit: { eles, padding: 40 } }, { duration: 450, easing: "ease-in-out-cubic" });
  else c.fit(eles, 40);
}

function zoomBy(c: Core, factor: number) {
  const level = Math.min(c.maxZoom(), Math.max(c.minZoom(), c.zoom() * factor));
  c.animate(
    { zoom: { level, renderedPosition: { x: c.width() / 2, y: c.height() / 2 } } },
    { duration: 160 },
  );
}

const renderOffKey = (pk: string) => `peon.attackSurface.renderOff.${pk}`;

function readRenderOff(pk: string): boolean {
  try {
    return window.localStorage.getItem(renderOffKey(pk)) === "1";
  } catch {
    return false;
  }
}

function writeRenderOff(pk: string, off: boolean) {
  try {
    if (off) window.localStorage.setItem(renderOffKey(pk), "1");
    else window.localStorage.removeItem(renderOffKey(pk));
  } catch {
    /* storage unavailable (private mode) — preference just won't persist */
  }
}

/** Plain-text hand-off an operator can paste into a prompt or ticket. */
function nodeKinds(node: SurfaceNode): string[] {
  const raw = node.types?.length ? node.types : [node.type || "other"];
  const out: string[] = [];
  for (const t of raw) {
    const slug = (t || "other").trim() || "other";
    if (!out.includes(slug)) out.push(slug);
  }
  return out.length ? out : ["other"];
}

function buildBrief(node: SurfaceNode, neighbours: Neighbour[]): string {
  const kinds = nodeKinds(node);
  const lines = [
    `Asset: ${node.value || node.label}`,
    `Type: ${kinds.map((k) => humanize(k)).join(", ")}`,
    `Class: ${node.class_label ?? humanize(node.type)}${node.oam ? ` (OAM ${node.oam})` : ""}`,
    `Scope: ${bucketOf(node.bucket).label}`,
    `Key: ${node.id}`,
  ];
  if (node.finding_count)
    lines.push(`Findings: ${node.finding_count}${node.severity ? ` (${node.severity})` : ""}`);
  if (node.first_seen) lines.push(`First seen: ${node.first_seen}`);
  if (node.last_seen) lines.push(`Last seen: ${node.last_seen}`);
  if (neighbours.length) {
    lines.push("Relations:");
    for (const { other, edge, dir } of neighbours.slice(0, 25)) {
      lines.push(
        `  ${dir === "out" ? "->" : "<-"} ${edge.title ?? edge.rel} ${other.value || other.label}`,
      );
    }
    if (neighbours.length > 25) lines.push(`  … +${neighbours.length - 25} more`);
  }
  return lines.join("\n");
}

function ToolButton({
  label,
  onClick,
  pressed,
  children,
}: {
  label: string;
  onClick: () => void;
  pressed?: boolean;
  children: React.ReactNode;
}) {
  return (
    <Button
      variant="ghost"
      size="icon"
      className={cn("size-8", pressed && "bg-accent text-accent-foreground")}
      aria-label={label}
      aria-pressed={pressed}
      title={label}
      onClick={onClick}
    >
      {children}
    </Button>
  );
}

export default function AttackSurface({ ctx, ops: initial }: { ctx: ProjectContext; ops: Ops }) {
  useLiveProject(ctx.pk);
  const { data: ops } = useOps(ctx.pk, initial);
  const surface = ops?.attack_surface;
  const theme = useTheme();
  const host = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);
  const centerNext = useRef(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [view, setView] = useState<"graph" | "list">("graph");
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState(false);
  const [renderOff, setRenderOff] = useState(() => (ctx.pk ? readRenderOff(ctx.pk) : false));
  const [layoutRun, setLayoutRun] = useState(0);
  const [copied, setCopied] = useState<"ok" | "err" | null>(null);

  const showGraph = view === "graph" && !renderOff;

  const nodes = useMemo(() => surface?.nodes ?? [], [surface]);
  const edges = useMemo(() => surface?.edges ?? [], [surface]);
  const legend = (surface?.legend ?? {}) as Legend;
  const typeColors = useMemo(() => {
    const slugs = new Set<string>();
    for (const n of nodes) for (const k of nodeKinds(n)) slugs.add(k);
    for (const g of legend.kinds ?? []) slugs.add(g.slug);
    return buildTypeColors(slugs);
  }, [nodes, legend.kinds]);
  const selected = useMemo(
    () => nodes.find((n) => n.id === selectedId) ?? null,
    [nodes, selectedId],
  );
  const signature = useMemo(
    () =>
      nodes
        .map((n) => `${n.id}:${n.type}:${n.bucket}:${n.finding_count}:${n.degree ?? 0}`)
        .join("|") + edges.map((e) => `${e.source}>${e.target}:${e.rel}`).join("|"),
    [nodes, edges],
  );

  const toggleRenderOff = useCallback(() => {
    setRenderOff((prev) => {
      const next = !prev;
      if (ctx.pk) writeRenderOff(ctx.pk, next);
      return next;
    });
  }, [ctx.pk]);

  useEffect(() => {
    if (!expanded && !selectedId) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (selectedId) setSelectedId(null);
      else setExpanded(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded, selectedId]);

  useEffect(() => {
    if (!expanded) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [expanded]);

  useEffect(() => {
    if (copied === null) return;
    const t = window.setTimeout(() => setCopied(null), 1600);
    return () => window.clearTimeout(t);
  }, [copied]);

  useEffect(() => {
    if (!host.current || !showGraph || !surface) return;
    const ids = new Set(surface.nodes.map((n) => n.id));
    const elements: ElementDefinition[] = [
      ...surface.nodes.map((n) => ({
        data: {
          id: n.id,
          label: n.label.length > 36 ? `${n.label.slice(0, 34)}…` : n.label,
          color: typeColors.get((n.type || "other").toLowerCase()) || colorForType(n.type || "other"),
          border: nodeBorderColor(n.type || "other"),
          size: n.center ? 40 : 20 + Math.min(18, (n.degree ?? 0) * 3),
          findings: n.finding_count ?? 0,
          kind: n.type || "other",
          vuln: isVulnType(n.type || "other") ? 1 : 0,
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
    const line = tokenColor("--border");
    const ring = tokenColor("--ring");
    const bg = tokenColor("--canvas");
    const card = tokenColor("--card");
    const warning = tokenColor("--warning") || "#F59E0B";
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    // cytoscape mutates its container, so give it a private child: React
    // never sees (and never trips over) the nodes cytoscape injects.
    const mount = document.createElement("div");
    mount.style.cssText = `position:absolute;inset:0;background:${bg}`;
    host.current.appendChild(mount);
    const instance = cytoscape({
      container: mount,
      elements,
      minZoom: 0.12,
      maxZoom: 3,
      wheelSensitivity: 0.35,
      style: [
        {
          selector: "node",
          style: {
            "background-color": "data(color)",
            "background-opacity": 1,
            // Circles: fill = type colour; border = white (red only for vulns).
            shape: "ellipse",
            width: "data(size)",
            height: "data(size)",
            label: "data(label)",
            color: fg,
            "font-size": 10,
            "font-weight": 500,
            "font-family": "Geist Variable, ui-sans-serif, sans-serif",
            "text-valign": "bottom",
            "text-margin-y": 6,
            "text-wrap": "ellipsis",
            "text-max-width": "140px",
            "text-background-color": card,
            "text-background-opacity": 0.7,
            "text-background-padding": "3px",
            "text-background-shape": "roundrectangle",
            "border-width": 2,
            "border-color": "data(border)",
            "border-opacity": 1,
            // Soft Amass-style halo — small, static (not a pulse).
            "underlay-color": "data(color)",
            "underlay-opacity": 0.1,
            "underlay-padding": 4,
            "underlay-shape": "ellipse",
            "overlay-opacity": 0,
            "overlay-padding": 0,
          },
        },
        {
          // Vulnerability types: red border (data(border) already set; reinforce width).
          selector: "node[?vuln]",
          style: {
            "border-width": 2.5,
            "underlay-color": VULN_BORDER,
            "underlay-opacity": 0.14,
            "underlay-padding": 5,
          },
        },
        {
          selector: "node:selected",
          style: {
            "border-color": ring,
            "border-width": 3,
            "underlay-color": ring,
            "underlay-opacity": 0.14,
            "underlay-padding": 6,
            "z-index": 999,
          },
        },
        { selector: "node:active", style: { "overlay-opacity": 0.04, "overlay-color": ring } },
        { selector: ".hidden", style: { display: "none" } },
        { selector: "node.faded", style: { opacity: 0.22, "underlay-opacity": 0 } },
        { selector: "edge.faded", style: { opacity: 0.08 } },
        {
          selector: "edge.lit",
          style: {
            width: 2.2,
            "line-color": ring,
            "target-arrow-color": ring,
            "line-style": "dashed",
            "line-dash-pattern": [7, 5],
            "arrow-scale": 0.9,
            opacity: 0.9,
          },
        },
        {
          selector: "node, edge",
          style: {
            "transition-property":
              "opacity, width, height, border-width, underlay-padding, underlay-opacity",
            "transition-duration": 180,
            "transition-timing-function": "ease-out",
          } as cytoscape.Css.Node,
        },
        {
          selector: "edge",
          style: {
            width: 1.35,
            "line-color": line,
            "target-arrow-color": line,
            "target-arrow-shape": "triangle",
            "arrow-scale": 0.75,
            "curve-style": "bezier",
            "control-point-step-size": 28,
            opacity: 0.65,
            label: "data(rel)",
            "font-size": 8,
            "font-weight": 500,
            color: muted,
            "text-rotation": "autorotate",
            "text-margin-y": -4,
            "text-background-color": card,
            "text-background-opacity": 0.65,
            "text-background-padding": "2px",
            "text-background-shape": "roundrectangle",
            "text-opacity": 0.7,
          },
        },
        {
          selector: "edge[derived = 1]",
          style: {
            width: 1,
            opacity: 0.55,
            "line-style": "dashed",
            "line-dash-pattern": [6, 4],
          },
        },
        {
          selector: "edge[status = 'unexpected']",
          style: {
            "line-style": "dashed",
            "line-color": warning,
            "target-arrow-color": warning,
            width: 1.75,
          },
        },
        { selector: "edge[status = 'custom']", style: { "line-style": "dotted", width: 1.5 } },
      ],
      layout: {
        name: "fcose",
        animate: !reduce,
        animationDuration: 900,
        animationEasing: "ease-out",
        quality: "proof",
        randomize: true,
        nodeDimensionsIncludeLabels: true,
        uniformNodeDimensions: false,
        packComponents: true,
        nodeRepulsion: () => 16000,
        idealEdgeLength: () => 120,
        edgeElasticity: () => 0.4,
        nestingFactor: 0.1,
        gravity: 0.35,
        gravityRange: 3.8,
        nodeSeparation: 96,
        padding: 48,
      } as cytoscape.LayoutOptions,
    });

    instance.one("layoutstop", () => fitGraph(instance, !reduce));

    instance.on("mouseover", "node", (e) => {
      const hood = e.target.closedNeighborhood();
      instance.batch(() => {
        instance.elements().not(hood).addClass("faded");
        hood.addClass("lit");
        e.target.style({ "underlay-padding": 7, "underlay-opacity": 0.16 });
      });
      mount.style.cursor = "pointer";
    });
    instance.on("mouseout", "node", (e) => {
      instance.batch(() => {
        instance.elements().removeClass("faded").removeClass("lit");
        const findings = Number(e.target.data("findings")) > 0;
        e.target.style({
          "underlay-padding": findings ? 5 : 4,
          "underlay-opacity": findings ? 0.12 : 0.1,
        });
      });
      mount.style.cursor = "grab";
    });
    instance.on("grab", "node", () => {
      mount.style.cursor = "grabbing";
    });
    instance.on("free", "node", () => {
      mount.style.cursor = "grab";
    });
    mount.style.cursor = "grab";

    // Relation arrows: marching dash on hover-lit edges + derived edges.
    let raf = 0;
    let dash = 0;
    const derived = instance.edges("[derived = 1]");
    const tick = () => {
      if (!reduce) {
        dash = (dash + 0.7) % 1000;
        instance.edges(".lit").style("line-dash-offset", -dash);
        derived.style("line-dash-offset", dash * 0.4);
      }
      raf = requestAnimationFrame(tick);
    };
    if (!reduce) raf = requestAnimationFrame(tick);

    instance.on("tap", "node", (e) => setSelectedId(e.target.id()));
    instance.on("tap", (e) => e.target === instance && setSelectedId(null));
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
    // eslint-disable-next-line react-hooks/exhaustive-deps -- rebuild only when the graph, theme or layout request changes
  }, [signature, theme, showGraph, layoutRun, typeColors]);

  // Type filter hides nodes (and their edges) without relayout, so positions stay stable.
  useEffect(() => {
    const c = cy.current;
    if (!c) return;
    c.batch(() => {
      c.nodes().forEach((n) => {
        n.toggleClass("hidden", hidden.has(String(n.data("kind"))));
      });
    });
    fitGraph(c);
  }, [hidden, signature, showGraph, layoutRun]);

  // Keep the canvas selection in step with the drawer (list rows, relation links).
  useEffect(() => {
    const c = cy.current;
    if (!c) return;
    c.batch(() => {
      c.nodes(":selected").unselect();
      if (selectedId) c.getElementById(selectedId).select();
    });
    if (selectedId && centerNext.current) {
      const node = c.getElementById(selectedId);
      if (node.nonempty() && node.visible())
        c.animate({ center: { eles: node } }, { duration: 300 });
    }
    centerNext.current = false;
  }, [selectedId, signature, showGraph, layoutRun]);

  const neighbours = useMemo<Neighbour[]>(() => {
    if (!selected) return [];
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const out: Neighbour[] = [];
    for (const e of edges) {
      if (e.source === selected.id && byId.has(e.target))
        out.push({ other: byId.get(e.target)!, edge: e, dir: "out" });
      else if (e.target === selected.id && byId.has(e.source))
        out.push({ other: byId.get(e.source)!, edge: e, dir: "in" });
    }
    return out;
  }, [selected, nodes, edges]);

  if (!nodes.length) {
    return (
      <div className="rounded-xl border border-dashed p-10 text-center text-sm text-muted-foreground">
        No assets yet — the graph fills in as agents and RoE add any subjects (hosts, paths, vulns,
        people, functions, …).
      </div>
    );
  }

  const kinds = legend.kinds ?? [];
  const toggleKind = (slug: string) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });
  const showAllKinds = () => setHidden(new Set());
  const hideAllKinds = () => setHidden(new Set(kinds.map((g) => g.slug)));

  const visibleNodes = nodes.filter((n) => nodeKinds(n).some((k) => !hidden.has(k)));
  const visibleIds = new Set(visibleNodes.map((n) => n.id));
  const visibleEdges = edges.filter(
    (e) => visibleIds.has(e.source) && visibleIds.has(e.target),
  ).length;

  const selectFromPanel = (id: string) => {
    centerNext.current = true;
    setSelectedId(id);
  };

  const copyBrief = async () => {
    if (!selected) return;
    try {
      await navigator.clipboard.writeText(buildBrief(selected, neighbours));
      setCopied("ok");
    } catch {
      setCopied("err");
    }
  };

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_18rem]">
      <section
        className={cn(
          "overflow-hidden border bg-card shadow-sm",
          expanded ? "fixed inset-0 z-50 flex flex-col rounded-none" : "rounded-xl",
        )}
        aria-label="Asset graph"
      >
        {/* Filter bar: colour = producer type on the canvas (open vocabulary). */}
        <div className="flex flex-wrap items-center gap-1.5 border-b px-3 py-2">
          <span className="mr-1 font-mono text-[10px] font-semibold tracking-widest text-muted-foreground uppercase">
            Filter
          </span>
          {kinds.map((g) => {
            const off = hidden.has(g.slug);
            return (
              <button
                key={g.slug}
                type="button"
                aria-pressed={!off}
                title={off ? `Show ${g.label}` : `Hide ${g.label}`}
                onClick={() => toggleKind(g.slug)}
                className={cn(
                  "inline-flex h-7 cursor-pointer items-center gap-1.5 rounded-md border px-2 text-xs font-medium transition-colors",
                  off
                    ? "border-dashed text-muted-foreground opacity-60"
                    : "bg-muted hover:bg-accent",
                )}
              >
                <TypeDot
                                    color={typeColors.get(g.slug.toLowerCase())}
                  className={off ? "opacity-50" : undefined}
                />
                <span className={cn(off && "line-through")}>{g.label}</span>
                <span className="tabular-nums text-muted-foreground">{g.count}</span>
              </button>
            );
          })}
          {kinds.length > 1 && (
            <span className="ml-1 inline-flex items-center gap-0.5 text-[11px]">
              <button
                type="button"
                onClick={showAllKinds}
                disabled={!hidden.size}
                className="cursor-pointer rounded px-1.5 py-0.5 text-muted-foreground hover:text-foreground disabled:cursor-default disabled:opacity-40"
              >
                All
              </button>
              <span className="text-border" aria-hidden>
                /
              </span>
              <button
                type="button"
                onClick={hideAllKinds}
                disabled={hidden.size === kinds.length}
                className="cursor-pointer rounded px-1.5 py-0.5 text-muted-foreground hover:text-foreground disabled:cursor-default disabled:opacity-40"
              >
                None
              </button>
            </span>
          )}
          <div className="ml-auto flex items-center gap-0.5">
            {showGraph && (
              <>
                <ToolButton label="Zoom in" onClick={() => cy.current && zoomBy(cy.current, 1.35)}>
                  <ZoomIn />
                </ToolButton>
                <ToolButton
                  label="Zoom out"
                  onClick={() => cy.current && zoomBy(cy.current, 1 / 1.35)}
                >
                  <ZoomOut />
                </ToolButton>
                <ToolButton label="Fit graph" onClick={() => cy.current && fitGraph(cy.current)}>
                  <Scan />
                </ToolButton>
                <ToolButton label="Re-run layout" onClick={() => setLayoutRun((n) => n + 1)}>
                  <RefreshCw />
                </ToolButton>
              </>
            )}
            {view === "graph" && (
              <ToolButton
                label={renderOff ? "Render graph (currently off)" : "Turn graph rendering off"}
                pressed={renderOff}
                onClick={toggleRenderOff}
              >
                <Power />
              </ToolButton>
            )}
            <span className="mx-1 h-5 w-px bg-border" aria-hidden />
            {view === "graph" && (
              <ToolButton
                label={expanded ? "Exit full screen (Esc)" : "Maximise graph"}
                pressed={expanded}
                onClick={() => setExpanded((v) => !v)}
              >
                {expanded ? <Minimize2 /> : <Maximize2 />}
              </ToolButton>
            )}
            <ToolButton
              label={view === "graph" ? "Show as list" : "Show as graph"}
              onClick={() => setView(view === "graph" ? "list" : "graph")}
            >
              {view === "graph" ? <List /> : <Network />}
            </ToolButton>
          </div>
        </div>

        {/* Key + live counts */}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b bg-muted/30 px-3 py-1.5 font-mono text-[11px] text-muted-foreground">
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
            <span
              className="size-2.5 rounded-full border-2 bg-transparent"
              style={{ borderColor: VULN_BORDER }}
              aria-hidden
            />
            Vulnerability
          </span>
          <span className="ml-auto tabular-nums" aria-live="polite">
            {visibleNodes.length}/{nodes.length} assets · {visibleEdges}/{edges.length} relations
          </span>
        </div>

        <div className={cn("relative", expanded ? "min-h-0 flex-1" : "h-[min(70vh,44rem)]")}>
          {showGraph ? (
            <div ref={host} data-attack-graph className="absolute inset-0 bg-canvas" />
          ) : view === "graph" ? (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-canvas p-6 text-center">
              <EyeOff className="size-6 text-muted-foreground" aria-hidden />
              <div className="space-y-1">
                <p className="text-sm font-medium">Graph rendering is off</p>
                <p className="text-xs text-muted-foreground">
                  {nodes.length} assets and {edges.length} relations are not being drawn. This
                  preference is remembered for this project.
                </p>
              </div>
              <Button size="sm" onClick={toggleRenderOff}>
                <Play /> Render graph
              </Button>
            </div>
          ) : (
            <div className="ui-table-wrap absolute inset-0 overflow-y-auto">
              <table className="ui-table">
                <thead>
                  <tr>
                    <th>Asset</th>
                    <th>Type</th>
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
                      onClick={() => setSelectedId(n.id)}
                    >
                      <td className="w-full max-w-0">
                        <span className="flex items-center gap-2">
                          <TypeDot
                                                        color={typeColors.get((n.type || "other").toLowerCase())}
                            borderColor={nodeBorderColor(n.type || "other")}
                          />
                          <span className="block truncate font-mono text-xs">
                            {n.value || n.label}
                          </span>
                        </span>
                      </td>
                      <td className="whitespace-nowrap">
                        {nodeKinds(n)
                          .map((k) => humanize(k))
                          .join(", ")}
                      </td>
                      <td>
                        <ToneBadge tone={bucketOf(n.bucket).tone}>
                          {bucketOf(n.bucket).label}
                        </ToneBadge>
                      </td>
                      <td className="text-right tabular-nums">{n.degree ?? 0}</td>
                      <td className="text-right tabular-nums">{n.finding_count ?? 0}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Node drawer */}
          {selected && (
            <aside
              className="absolute inset-y-0 right-0 z-10 flex w-[min(22rem,100%)] flex-col border-l bg-card/95 shadow-xl backdrop-blur-sm"
              aria-label="Asset details"
            >
              <header className="flex items-start gap-2 border-b p-3">
                <TypeDot
                                    color={typeColors.get((selected.type || "other").toLowerCase())}
                  borderColor={nodeBorderColor(selected.type || "other")}
                  className="mt-1.5 size-3.5"
                />
                <div className="min-w-0 flex-1 space-y-0.5">
                  <p className="font-mono text-[10px] font-semibold tracking-widest text-muted-foreground uppercase">
                    {nodeKinds(selected)
                      .map((k) => humanize(k))
                      .join(" · ")}
                  </p>
                  <h3 className="font-mono text-[13px] font-medium break-all">
                    {selected.value || selected.label}
                  </h3>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-7 shrink-0"
                  aria-label="Close details"
                  title="Close (Esc)"
                  onClick={() => setSelectedId(null)}
                >
                  <X />
                </Button>
              </header>

              <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3 text-sm">
                <div className="flex flex-wrap gap-1.5">
                  <ToneBadge tone={bucketOf(selected.bucket).tone}>
                    {bucketOf(selected.bucket).label}
                  </ToneBadge>
                  {selected.severity && (
                    <ToneBadge tone={statusTone(selected.severity)}>
                      {humanize(selected.severity)}
                    </ToneBadge>
                  )}
                  {!!selected.finding_count && (
                    <ToneBadge tone="warning">{selected.finding_count} findings</ToneBadge>
                  )}
                </div>

                <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-xs">
                  {(
                    [
                      ["Label", selected.label],
                      [
                        "Type",
                        nodeKinds(selected)
                          .map((k) => humanize(k))
                          .join(", "),
                      ],
                      [
                        "Class",
                        selected.class_label && selected.class_label !== humanize(selected.type)
                          ? `${selected.class_label}${selected.oam ? ` · OAM ${selected.oam}` : ""}`
                          : selected.oam
                            ? `OAM ${selected.oam}`
                            : undefined,
                      ],
                      ["Bucket", bucketOf(selected.bucket).label],
                      ["Key", selected.id],
                      ["Title", selected.title],
                      ["Status", selected.status && humanize(selected.status)],
                      [
                        "First seen",
                        selected.first_seen && new Date(selected.first_seen).toLocaleString(),
                      ],
                    ] as [string, string | undefined | null | false][]
                  )
                    .filter(([, v]) => v)
                    .map(([k, v]) => (
                      <div key={k} className="contents">
                        <dt className="text-muted-foreground">{k}</dt>
                        <dd className={cn("break-all", k === "Key" && "font-mono")}>{v}</dd>
                      </div>
                    ))}
                </dl>

                {neighbours.length > 0 && (
                  <section className="space-y-1.5">
                    <h4 className="font-mono text-[10px] font-semibold tracking-widest text-muted-foreground uppercase">
                      Relations · {neighbours.length}
                    </h4>
                    <ul className="space-y-0.5">
                      {neighbours.slice(0, 40).map(({ other, edge, dir }) => (
                        <li key={`${edge.source}>${edge.target}:${edge.rel}`}>
                          <button
                            type="button"
                            onClick={() => selectFromPanel(other.id)}
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
                    <h4 className="font-mono text-[10px] font-semibold tracking-widest text-muted-foreground uppercase">
                      Observed by
                    </h4>
                    <ul className="space-y-1 text-xs">
                      {selected.sources.map((s) => (
                        <li key={s.name} className="flex items-baseline justify-between gap-2">
                          <span className="truncate">
                            {s.name === "rules_of_engagement"
                              ? "Rules of Engagement"
                              : s.name === "finding"
                                ? "Finding subject"
                                : s.name}
                          </span>
                          <span
                            className="shrink-0 tabular-nums text-muted-foreground"
                            title={`first ${s.first_seen}`}
                          >
                            {s.last_seen ? new Date(s.last_seen).toLocaleString() : ""}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </section>
                )}
              </div>

              <footer className="border-t p-3">
                <Button
                  variant="outline"
                  size="sm"
                  className="w-full"
                  onClick={copyBrief}
                  aria-live="polite"
                >
                  {copied === "ok" ? <Check /> : <Copy />}
                  {copied === "ok"
                    ? "Brief copied"
                    : copied === "err"
                      ? "Copy failed"
                      : "Copy brief"}
                </Button>
              </footer>
            </aside>
          )}
        </div>

        {!!legend.relation_counts?.length && (
          <div className="flex flex-wrap gap-1.5 border-t px-3 py-2">
            {legend.relation_counts.map((r) => (
              <ToneBadge key={r.label} tone={r.status === "unexpected" ? "warning" : "neutral"}>
                {r.title} · {r.count}
              </ToneBadge>
            ))}
          </div>
        )}
      </section>

      <aside
        className="rounded-xl border bg-card p-4 shadow-sm xl:self-start"
        aria-label="Surface overview"
      >
        <div className="space-y-3 text-sm">
          <h3 className="font-mono text-[10px] font-semibold tracking-widest text-muted-foreground uppercase">
            Surface
          </h3>
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
          {!!kinds.length && (
            <ul className="space-y-1 text-xs">
              {kinds.slice(0, 12).map((g) => (
                <li key={g.slug} className="flex items-center justify-between gap-2">
                  <span className="inline-flex min-w-0 items-center gap-1.5">
                    <TypeDot color={typeColors.get(g.slug.toLowerCase())} />
                    <span className="truncate">{g.label}</span>
                  </span>
                  <span className="tabular-nums text-muted-foreground">{g.count}</span>
                </li>
              ))}
            </ul>
          )}
          <p className="text-xs text-muted-foreground">
            Nodes are typed by producers — any subject a role invents. Click one for details.
          </p>
        </div>
      </aside>
    </div>
  );
}
