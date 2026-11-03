import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderOpen, Loader2, Plus, Save, Sparkles, Trash2, Upload } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api } from "@/lib/api";
import Canvas, { type Selection } from "./workflow/Canvas";
import Inspector from "./workflow/Inspector";
import { autoLayout, reflowAfterResize } from "./workflow/layout";
import Palette from "./workflow/Palette";
import PanelWindow from "./workflow/PanelWindow";
import PlanPanel from "./workflow/PlanPanel";
import SeedInputs from "./workflow/SeedInputs";
import {
  type Catalog,
  type EdgeKind,
  type Graph,
  type LintResult,
  type NodeKind,
  type PlanPreview,
  type SeedInput,
  type WFNode,
  type WorkflowRow,
  canLink,
} from "./workflow/types";

type Props = {
  catalogUrl: string;
  listUrl: string;
  detailUrl: string; // contains "{id}"
  createUrl: string;
  writeUrl: string; // contains "{id}"
  deleteUrl: string; // contains "{id}"
  lintUrl: string;
  compileUrl: string;
  generateUrl: string;
  projectsUrl: string;
  fromProjectUrl: string;
};

type ProjectOption = { id: string; title: string; summary: string; status: string };

const blankGraph = (): Graph => ({ nodes: [], edges: [] });
const uid = (p: string) => `${p}${Date.now().toString(36)}${Math.floor(Math.random() * 1296).toString(36)}`;

/**
 * Preset-workflow designer. The canvas is a hierarchy that executes: project manager → roles → skills → tools.
 * Every change is checked live by the server, which also shows the exact objectives the workflow compiles to;
 * only a workflow that compiles can be published, and project creation runs that plan directly.
 */
export default function WorkflowDesigner({
  catalogUrl,
  listUrl,
  detailUrl,
  createUrl,
  writeUrl,
  deleteUrl,
  compileUrl,
  generateUrl,
  projectsUrl,
  fromProjectUrl,
}: Props) {
  const qc = useQueryClient();
  const { data: catalog } = useQuery({
    queryKey: ["workflow-designer-catalog"],
    queryFn: () => api<Catalog & { ok: boolean }>(catalogUrl),
  });
  const { data: workflows } = useQuery({
    queryKey: ["workflow-designer-list"],
    queryFn: () => api<{ ok: boolean; workflows: WorkflowRow[] }>(listUrl).then((r) => r.workflows),
  });
  const { data: projects } = useQuery({
    queryKey: ["workflow-designer-projects"],
    queryFn: () => api<{ ok: boolean; projects: ProjectOption[] }>(projectsUrl).then((r) => r.projects),
  });

  const [workflowId, setWorkflowId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [graph, setGraph] = useState<Graph>(blankGraph());
  const [seeds, setSeeds] = useState<SeedInput[]>([]);
  const [isPublished, setIsPublished] = useState(false);
  const [selection, setSelection] = useState<Selection>(null);
  const [busy, setBusy] = useState(false);
  const [lint, setLint] = useState<LintResult>({ errors: [], warnings: [] });
  const [plan, setPlan] = useState<PlanPreview | null>(null);
  const [checking, setChecking] = useState(false);
  const [startMode, setStartMode] = useState<"goal" | "project">("goal");
  const [goal, setGoal] = useState("");
  const [generating, setGenerating] = useState(false);
  const [nameOpen, setNameOpen] = useState(false);
  const [projectId, setProjectId] = useState("");
  const [deducing, setDeducing] = useState(false);
  const nameBox = useRef<HTMLDivElement>(null);

  const refresh = () => qc.invalidateQueries({ queryKey: ["workflow-designer-list"] });

  const load = (w: Partial<WorkflowRow>) => {
    setWorkflowId(w.workflow_id ?? null);
    setName(w.name ?? "");
    setDescription(w.description ?? "");
    setGraph(w.graph?.nodes ? w.graph : blankGraph());
    setSeeds(w.seed_inputs ?? []);
    setIsPublished(!!w.is_published);
    setSelection(null);
    setNameOpen(false);
  };

  const open = async (id: string) => {
    try {
      const res = await api<{ ok: boolean; workflow: WorkflowRow }>(detailUrl.replace("{id}", encodeURIComponent(id)));
      load(res.workflow);
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  const nameSuggestions = useMemo(() => {
    const q = name.trim().toLowerCase();
    const all = workflows ?? [];
    if (!q) return all.slice(0, 8);
    return all
      .filter((w) => `${w.name} ${w.workflow_id}`.toLowerCase().includes(q))
      .slice(0, 8);
  }, [workflows, name]);

  useEffect(() => {
    const onDoc = (ev: MouseEvent) => {
      if (!nameBox.current?.contains(ev.target as Node)) setNameOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  // ── live check: lint + compiled plan, debounced ────────────────────────────
  useEffect(() => {
    if (graph.nodes.length === 0) return;
    const t = setTimeout(async () => {
      setChecking(true);
      try {
        const r = await api<{ ok: boolean; plan: PlanPreview | null } & LintResult>(compileUrl, {
          json: { graph, seed_inputs: seeds, name, description },
        });
        setLint({ errors: r.errors, warnings: r.warnings });
        setPlan(r.plan);
      } catch (e) {
        setLint({ errors: [(e as Error).message], warnings: [] });
        setPlan(null);
      } finally {
        setChecking(false);
      }
    }, 600);
    return () => clearTimeout(t);
  }, [graph, seeds, name, description, compileUrl]);

  // ── graph edits ────────────────────────────────────────────────────────────
  const byId = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph.nodes]);
  const selectedNode = selection?.type === "node" ? byId.get(selection.id) : undefined;
  const usedRefs = useMemo(() => new Set(graph.nodes.map((n) => `${n.kind}:${n.ref_id}`)), [graph.nodes]);
  const linkable = useMemo(() => {
    const set = new Set<NodeKind>();
    if (!selectedNode) return set;
    (["role", "skill", "tool"] as NodeKind[]).forEach((k) => {
      if (canLink(selectedNode.kind, k)) set.add(k);
    });
    return set;
  }, [selectedNode]);
  const problemNodes = useMemo(() => {
    const ids = new Set<string>();
    for (const e of lint.errors ?? []) {
      const m = /node ['"]?([a-zA-Z0-9_-]+)/.exec(e);
      if (m?.[1]) ids.add(m[1]);
    }
    return ids;
  }, [lint]);

  const setConfig = (id: string, patch: Record<string, string>) =>
    setGraph((g) => ({
      ...g,
      nodes: g.nodes.map((n) => (n.id === id ? { ...n, config: { ...n.config, ...patch } } : n)),
    }));
  const setEdgeKind = (id: string, kind: EdgeKind) =>
    setGraph((g) => ({ ...g, edges: g.edges.map((e) => (e.id === id ? { ...e, kind } : e)) }));
  const removeNode = (id: string) => {
    setGraph((g) => ({
      nodes: g.nodes.filter((n) => n.id !== id),
      edges: g.edges.filter((e) => e.source !== id && e.target !== id),
    }));
    setSelection(null);
  };
  const removeEdge = (id: string) => {
    setGraph((g) => ({ ...g, edges: g.edges.filter((e) => e.id !== id) }));
    setSelection(null);
  };
  const addNode = (kind: NodeKind, refId: string, at?: { x: number; y: number }) => {
    const option =
      kind === "role"
        ? catalog?.roles.find((o) => o.id === refId)
        : kind === "skill"
          ? catalog?.skills.find((o) => o.id === refId)
          : catalog?.tools.find((o) => o.id === refId);
    const spot = at ?? { x: 40 + graph.nodes.length * 24, y: 40 + graph.nodes.length * 18 };
    const node: WFNode = {
      id: uid("n"),
      kind,
      ref_id: refId,
      label: option?.label ?? refId,
      x: Math.max(0, spot.x),
      y: Math.max(0, spot.y),
      config: {},
    };
    setGraph((g) => {
      const next = { ...g, nodes: [...g.nodes, node] };
      if (selectedNode && canLink(selectedNode.kind, kind)) {
        next.edges = [
          ...g.edges,
          { id: uid("e"), source: selectedNode.id, target: node.id, kind: "sequence" as EdgeKind },
        ];
      }
      return next;
    });
    setSelection({ type: "node", id: node.id });
  };
  const connect = (source: string, target: string) => {
    const a = byId.get(source);
    const b = byId.get(target);
    if (!a || !b || !canLink(a.kind, b.kind)) return;
    setGraph((g) => {
      if (g.edges.some((e) => e.source === source && e.target === target)) return g;
      return { ...g, edges: [...g.edges, { id: uid("e"), source, target, kind: "sequence" }] };
    });
  };
  const moveNode = (id: string, x: number, y: number) =>
    setGraph((g) => ({ ...g, nodes: g.nodes.map((n) => (n.id === id ? { ...n, x, y } : n)) }));
  const resizeNode = (id: string, w: number, h: number) =>
    setGraph((g) =>
      reflowAfterResize(
        { ...g, nodes: g.nodes.map((n) => (n.id === id ? { ...n, w, h } : n)) },
        id,
      ),
    );
  const deleteSelection = () => {
    if (selection?.type === "node") removeNode(selection.id);
    else if (selection?.type === "edge") removeEdge(selection.id);
  };

  const onNameChange = (value: string) => {
    setName(value);
    setNameOpen(true);
    // Typing a brand-new name (no exact match) starts a fresh draft identity.
    const exact = (workflows ?? []).find((w) => w.name.toLowerCase() === value.trim().toLowerCase());
    if (!exact && workflowId) {
      const current = (workflows ?? []).find((w) => w.workflow_id === workflowId);
      if (current && current.name.toLowerCase() !== value.trim().toLowerCase()) {
        // Keep editing the open record (rename on save) unless the field is emptied.
        if (!value.trim()) setWorkflowId(null);
      }
    } else if (!exact && !value.trim()) {
      setWorkflowId(null);
    }
  };

  const pickSuggestion = (w: WorkflowRow) => {
    void open(w.workflow_id);
  };

  // ── persistence ────────────────────────────────────────────────────────────
  const save = async (publish: boolean) => {
    if (!name.trim()) return void toast.error("Give the workflow a name");
    setBusy(true);
    try {
      const payload = { name, description, seed_inputs: seeds, graph, is_published: publish };
      const res = workflowId
        ? await api<{ ok: boolean; workflow: WorkflowRow }>(writeUrl.replace("{id}", encodeURIComponent(workflowId)), { json: payload })
        : await api<{ ok: boolean; workflow: WorkflowRow }>(createUrl, { json: payload });
      setWorkflowId(res.workflow.workflow_id);
      setIsPublished(res.workflow.is_published);
      toast.success(publish ? "Published — it now shows up when creating a project" : "Draft saved");
      await refresh();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!workflowId) return;
    try {
      await api(deleteUrl.replace("{id}", encodeURIComponent(workflowId)), { method: "DELETE" });
      toast.success("Deleted");
      load({});
      await refresh();
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  const applyDraft = (w: Partial<WorkflowRow> & { lint?: LintResult }, label: string) => {
    load({ name: w.name, description: w.description, graph: w.graph, seed_inputs: w.seed_inputs });
    const errors = w.lint?.errors?.length ?? 0;
    toast.success(
      errors
        ? `${label} with ${errors} open problem(s) — fix them before publishing`
        : `${label} — ${w.graph?.nodes?.length ?? 0} cards. Review the plan, then save`,
    );
  };

  const generate = async () => {
    setGenerating(true);
    try {
      const res = await api<{ ok: boolean; workflow: Partial<WorkflowRow> & { lint: LintResult } }>(generateUrl, { json: { goal } });
      applyDraft(res.workflow, "Drafted");
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setGenerating(false);
    }
  };

  const deduceFromProject = async () => {
    if (!projectId) return void toast.error("Pick a project first");
    setDeducing(true);
    try {
      const res = await api<{ ok: boolean; workflow: Partial<WorkflowRow> & { lint: LintResult } }>(fromProjectUrl, {
        json: { project_id: projectId },
      });
      applyDraft(res.workflow, "Deduced from project");
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setDeducing(false);
    }
  };

  const empty = graph.nodes.length === 0;
  const shownLint = empty ? { errors: [], warnings: [] } : lint;
  const shownPlan = empty ? null : plan;
  const canPublish = !checking && shownLint.errors.length === 0 && !empty;
  const editingHint = workflowId
    ? `Editing ${isPublished ? "published" : "draft"} · ${workflowId}`
    : name.trim()
      ? "New workflow — save to create"
      : "Type a name or pick an existing workflow";

  return (
    <div className="space-y-4">
      <div className="space-y-3 rounded-xl border bg-card p-3 shadow-sm">
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-56 flex-1 space-y-1" ref={nameBox}>
            <Label className="text-xs">Name</Label>
            <div className="relative">
              <Input
                className="h-9"
                value={name}
                placeholder="Workflow name…"
                autoComplete="off"
                onFocus={() => setNameOpen(true)}
                onChange={(e) => onNameChange(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") setNameOpen(false);
                  if (e.key === "ArrowDown" && nameSuggestions[0]) {
                    e.preventDefault();
                    setNameOpen(true);
                  }
                  if (e.key === "Enter" && nameOpen && nameSuggestions[0]) {
                    e.preventDefault();
                    pickSuggestion(nameSuggestions[0]);
                  }
                }}
              />
              {nameOpen && nameSuggestions.length > 0 && (
                <ul className="absolute z-20 mt-1 max-h-56 w-full overflow-auto rounded-md border bg-popover p-1 text-sm shadow-md">
                  {nameSuggestions.map((w) => (
                    <li key={w.workflow_id}>
                      <button
                        type="button"
                        className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left hover:bg-accent"
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={() => pickSuggestion(w)}
                      >
                        <span className="min-w-0 flex-1 truncate font-medium">{w.name || w.workflow_id}</span>
                        <span className="shrink-0 text-[10px] text-muted-foreground">
                          {w.is_published ? "published" : "draft"}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <p className="text-[11px] text-muted-foreground">{editingHint}</p>
          </div>
          <div className="flex items-center gap-2 pt-5">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => load({})}
              title="Start a blank workflow"
            >
              <Plus className="size-4" /> New
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => save(false)}>
              {busy ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />} Save draft
            </Button>
            <Button disabled={busy || !canPublish} onClick={() => save(true)} title={canPublish ? "" : "Resolve the problems in the plan first"}>
              <Upload className="size-4" /> Publish
            </Button>
            {workflowId && (
              <Button variant="ghost" size="icon" aria-label="Delete workflow" onClick={remove}>
                <Trash2 className="size-4" />
              </Button>
            )}
            {isPublished && <span className="text-xs text-success">Published</span>}
          </div>
        </div>
        <div className="space-y-1">
          <Label className="text-xs">What it is for</Label>
          <Input
            className="h-9"
            value={description}
            placeholder="Short description — when to use this workflow"
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
      </div>

      <div className="space-y-3 rounded-xl border bg-card p-3 shadow-sm">
        <div role="tablist" aria-label="Start a workflow from" className="inline-flex rounded-lg border bg-muted p-0.5">
          <button
            type="button"
            role="tab"
            aria-selected={startMode === "goal"}
            onClick={() => setStartMode("goal")}
            className="h-7 cursor-pointer rounded-md px-3 text-xs font-medium text-muted-foreground aria-selected:bg-background aria-selected:text-foreground aria-selected:shadow-sm"
          >
            <Sparkles className="mr-1.5 inline size-3.5" /> From a goal
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={startMode === "project"}
            onClick={() => setStartMode("project")}
            className="h-7 cursor-pointer rounded-md px-3 text-xs font-medium text-muted-foreground aria-selected:bg-background aria-selected:text-foreground aria-selected:shadow-sm"
          >
            <FolderOpen className="mr-1.5 inline size-3.5" /> From a project
          </button>
        </div>
        {startMode === "goal" ? (
          <div className="flex flex-wrap items-center gap-2">
            <Input
              className="h-9 min-w-64 flex-1"
              value={goal}
              placeholder="Describe the goal and let the catalog draft the workflow — e.g. “map a domain's external surface, then write a report”"
              onChange={(e) => setGoal(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && goal.trim().length >= 8 && !generating) generate();
              }}
            />
            <Button variant="secondary" disabled={generating || goal.trim().length < 8} onClick={generate}>
              {generating ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />} Draft with AI
            </Button>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <Select value={projectId || undefined} onValueChange={setProjectId}>
              <SelectTrigger className="h-9 min-w-64 flex-1">
                <SelectValue placeholder="Pick an existing project…" />
              </SelectTrigger>
              <SelectContent>
                {(projects ?? []).map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.title}
                    {p.status !== "active" ? ` (${p.status})` : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button variant="secondary" disabled={deducing || !projectId} onClick={deduceFromProject}>
              {deducing ? <Loader2 className="size-4 animate-spin" /> : <FolderOpen className="size-4" />} Deduce workflow
            </Button>
          </div>
        )}
      </div>

      <div className="wf-desk flex h-[min(70dvh,52rem)] min-h-[36rem] flex-col gap-3">
        <div className="flex min-h-0 flex-1 gap-3">
          <PanelWindow id="catalog" title="Catalog" dock="left" defaultSize={220} minSize={160} maxSize={420}>
            <Palette catalog={catalog} linkable={linkable} usedRefs={usedRefs} onAdd={(k, r) => addNode(k, r)} />
          </PanelWindow>
          <PanelWindow
            id="canvas"
            title="Workflow"
            dock="center"
            defaultSize={520}
            className="min-w-0"
            headerExtra={
              <span className="hidden text-xs text-muted-foreground md:inline">
                Collapse any card · drag edges to resize · node corner to size · wheel to zoom
              </span>
            }
          >
            <Canvas
              graph={graph}
              catalog={catalog}
              selection={selection}
              problemNodes={problemNodes}
              onSelect={setSelection}
              onMove={moveNode}
              onResize={resizeNode}
              onConnect={connect}
              onDropCatalog={(k, r, x, y) => addNode(k, r, { x, y })}
              onDeleteSelection={deleteSelection}
              onAutoLayout={() => setGraph((g) => autoLayout(g))}
            />
          </PanelWindow>
          <PanelWindow id="inspector" title="Inspector" dock="right" defaultSize={288} minSize={180} maxSize={520}>
            <Inspector
              graph={graph}
              selection={selection}
              catalog={catalog}
              seeds={seeds}
              onConfig={setConfig}
              onEdgeKind={setEdgeKind}
              onRemoveNode={removeNode}
              onRemoveEdge={removeEdge}
            />
          </PanelWindow>
        </div>
        <div className="flex min-h-0 shrink-0 gap-3">
          <PanelWindow id="seeds" title="Inputs" dock="bottom" defaultSize={200} minSize={100} maxSize={420} className="min-w-0 flex-1">
            <SeedInputs seeds={seeds} onChange={setSeeds} />
          </PanelWindow>
          <PanelWindow id="plan" title="Execution plan" dock="bottom" defaultSize={200} minSize={100} maxSize={420} className="min-w-0 flex-1">
            <PlanPanel lint={shownLint} plan={shownPlan} checking={checking} />
          </PanelWindow>
        </div>
      </div>
    </div>
  );
}
