import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import type { Selection } from "./Canvas";
import { type Catalog, type EdgeKind, type Graph, PHASES, type SeedInput, type WFNode } from "./types";

type Props = {
  graph: Graph;
  selection: Selection;
  catalog: Catalog | undefined;
  seeds: SeedInput[];
  onConfig: (id: string, patch: Record<string, string>) => void;
  onEdgeKind: (id: string, kind: EdgeKind) => void;
  onRemoveNode: (id: string) => void;
  onRemoveEdge: (id: string) => void;
};

export default function Inspector({ graph, selection, catalog, seeds, onConfig, onEdgeKind, onRemoveNode, onRemoveEdge }: Props) {
  const node = selection?.type === "node" ? graph.nodes.find((n) => n.id === selection.id) : undefined;
  const edge = selection?.type === "edge" ? graph.edges.find((e) => e.id === selection.id) : undefined;

  return (
    <div className="h-full space-y-3 overflow-y-auto p-3">
      {node ? (
        <NodeForm node={node} graph={graph} catalog={catalog} seeds={seeds} onConfig={onConfig} onRemove={onRemoveNode} />
      ) : edge ? (
        <EdgeForm edge={edge} graph={graph} onKind={onEdgeKind} onRemove={onRemoveEdge} />
      ) : (
        <div className="space-y-2 text-sm text-muted-foreground">
          <h3 className="text-sm font-semibold text-foreground">How it fits together</h3>
          <p>
            Build the hierarchy top-down: the <b>project manager</b> on top, specialist <b>roles</b> beneath it, the
            <b> skills</b> each role runs under that role, and the <b>tools</b> a step needs under the role or skill.
          </p>
          <p>
            Drag from a card's bottom dot onto another card to link. A <b>sequence</b> link means “after”, a
            <b> parallel</b> link means “beside”. Select a card to say exactly what it should do.
          </p>
          <p>The plan below is what a project created from this workflow will run.</p>
        </div>
      )}
    </div>
  );
}

function NodeForm({
  node,
  graph,
  catalog,
  seeds,
  onConfig,
  onRemove,
}: {
  node: WFNode;
  graph: Graph;
  catalog: Catalog | undefined;
  seeds: SeedInput[];
  onConfig: Props["onConfig"];
  onRemove: Props["onRemoveNode"];
}) {
  const list = node.kind === "role" ? catalog?.roles : node.kind === "skill" ? catalog?.skills : catalog?.tools;
  const info = list?.find((o) => o.id === node.ref_id);
  const cfg = node.config ?? {};
  const kids = graph.edges
    .filter((e) => e.source === node.id)
    .map((e) => graph.nodes.find((n) => n.id === e.target))
    .filter((n): n is WFNode => !!n);
  const insert = (field: "instruction" | "acceptance", key: string) =>
    onConfig(node.id, { [field]: `${(cfg[field] as string) ?? ""}${(cfg[field] as string)?.endsWith(" ") || !cfg[field] ? "" : " "}{{${key}}}` });

  return (
    <div className="space-y-3 text-sm">
      <div>
        <p className="text-[11px] tracking-wide text-muted-foreground uppercase">{node.kind}</p>
        <p className="font-mono text-[13px] font-medium">{node.ref_id}</p>
        {info?.description && <p className="mt-1 text-xs text-muted-foreground">{info.description}</p>}
      </div>

      {node.kind === "role" ? (
        <>
          <div className="space-y-1.5">
            <Label className="text-xs">Step title</Label>
            <Input value={(cfg.title as string) ?? ""} placeholder="defaults to the role and its skills" onChange={(e) => onConfig(node.id, { title: e.target.value })} />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">Instruction</Label>
            <Textarea
              rows={6}
              value={(cfg.instruction as string) ?? ""}
              placeholder="What exactly should this role do? Be concrete and checkable."
              onChange={(e) => onConfig(node.id, { instruction: e.target.value })}
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">Done when</Label>
            <Textarea
              rows={3}
              value={(cfg.acceptance as string) ?? ""}
              placeholder="How do we know this step is finished?"
              onChange={(e) => onConfig(node.id, { acceptance: e.target.value })}
            />
          </div>
          {seeds.length > 0 && (
            <div className="space-y-1">
              <Label className="text-xs">Insert an input</Label>
              <div className="flex flex-wrap gap-1">
                {seeds.map((s) => (
                  <button
                    key={s.key}
                    type="button"
                    onClick={() => insert("instruction", s.key)}
                    className="rounded-md border px-1.5 py-0.5 font-mono text-[11px] hover:bg-accent"
                    title={`Append {{${s.key}}} to the instruction`}
                  >
                    {`{{${s.key}}}`}
                  </button>
                ))}
              </div>
            </div>
          )}
          <div className="space-y-1.5">
            <Label className="text-xs">Phase</Label>
            <Select value={(cfg.phase as string) || PHASES[0]} onValueChange={(v) => onConfig(node.id, { phase: v })}>
              <SelectTrigger className="h-8 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PHASES.map((p) => (
                  <SelectItem key={p} value={p}>
                    {p}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </>
      ) : node.kind === "skill" ? (
        <p className="text-xs text-muted-foreground">
          The owning role loads this skill for its step. It can only sit under a role it is bound to
          {info?.bound?.length ? ` (${info.bound.join(", ")})` : ""}.
        </p>
      ) : (
        <p className="text-xs text-muted-foreground">The CLI is provisioned in the sandbox before the step starts.</p>
      )}

      {kids.length > 0 && (
        <div className="space-y-1">
          <Label className="text-xs">Connected below</Label>
          <ul className="flex flex-wrap gap-1">
            {kids.map((k) => (
              <li key={k.id} className="org-agent-tag">
                {k.kind}: {k.ref_id}
              </li>
            ))}
          </ul>
        </div>
      )}
      <Button size="sm" variant="destructive" onClick={() => onRemove(node.id)}>
        <Trash2 className="size-3.5" /> Remove card
      </Button>
    </div>
  );
}

function EdgeForm({ edge, graph, onKind, onRemove }: { edge: Graph["edges"][number]; graph: Graph; onKind: Props["onEdgeKind"]; onRemove: Props["onRemoveEdge"] }) {
  const from = graph.nodes.find((n) => n.id === edge.source);
  const to = graph.nodes.find((n) => n.id === edge.target);
  const roleToRole = from?.kind === "role" && to?.kind === "role";
  return (
    <div className="space-y-3 text-sm">
      <div>
        <p className="text-[11px] tracking-wide text-muted-foreground uppercase">link</p>
        <p className="font-mono text-[13px]">
          {from?.ref_id} → {to?.ref_id}
        </p>
      </div>
      {roleToRole ? (
        <div className="space-y-1.5">
          <Label className="text-xs">Order</Label>
          <Select value={edge.kind} onValueChange={(v) => onKind(edge.id, v as EdgeKind)}>
            <SelectTrigger className="h-8 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="sequence">after — starts when the parent has finished</SelectItem>
              <SelectItem value="parallel">beside — starts with the parent, shares its upstream</SelectItem>
            </SelectContent>
          </Select>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">
          {to?.kind === "skill" ? "The role runs this skill." : "The role / skill needs this tool."}
        </p>
      )}
      <Button size="sm" variant="destructive" onClick={() => onRemove(edge.id)}>
        <Trash2 className="size-3.5" /> Remove link
      </Button>
    </div>
  );
}
