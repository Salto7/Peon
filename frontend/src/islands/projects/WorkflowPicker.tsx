import { useEffect, useId, useMemo, useState } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { api } from "@/lib/api";

type SeedInput = {
  key: string;
  label: string;
  kind: "text" | "scope";
  required: boolean;
  maps_to: "summary" | "scope";
};
type GraphNode = { kind: "role" | "skill" | "tool"; ref_id: string };
type WorkflowRow = {
  workflow_id: string;
  name: string;
  description: string;
  seed_inputs: SeedInput[];
  /** From ``WorkflowPreset.is_published`` — drafts are selectable on create. */
  is_published?: boolean;
  graph?: { nodes?: GraphNode[] };
};

const count = (w: WorkflowRow, kind: GraphNode["kind"]) =>
  (w.graph?.nodes ?? []).filter((n) => n.kind === kind).length;

type Props = { listUrl: string };

export default function WorkflowPicker({ listUrl }: Props) {
  const ids = useId();
  const [workflows, setWorkflows] = useState<WorkflowRow[]>([]);
  const [enabled, setEnabled] = useState(false);
  const [workflowId, setWorkflowId] = useState("");

  useEffect(() => {
    api<{ ok: boolean; workflows: WorkflowRow[] }>(listUrl)
      .then((r) => setWorkflows(r.workflows))
      .catch(() => setWorkflows([]));
  }, [listUrl]);

  useEffect(() => {
    const active = enabled && !!workflowId;
    document.querySelectorAll<HTMLElement>("[data-manual-section]").forEach((el) => {
      el.hidden = active;
    });
    return () => {
      document.querySelectorAll<HTMLElement>("[data-manual-section]").forEach((el) => {
        el.hidden = false;
      });
    };
  }, [enabled, workflowId]);

  const selected = useMemo(
    () => workflows.find((w) => w.workflow_id === workflowId) ?? null,
    [workflows, workflowId],
  );

  if (workflows.length === 0) return null;

  return (
    <div className="space-y-4 rounded-xl border bg-card p-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm font-medium">Use a preset workflow</p>
          <p className="text-xs text-muted-foreground">
            Pick a designer-built workflow instead of filling in scope and crew by hand.
          </p>
        </div>
        <Switch checked={enabled} onCheckedChange={setEnabled} aria-label="Use a preset workflow" />
      </div>

      {enabled && (
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label id={`${ids}-workflow`}>Workflow</Label>
            <div
              role="radiogroup"
              aria-labelledby={`${ids}-workflow`}
              className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4"
            >
              {workflows.map((w) => {
                const on = w.workflow_id === workflowId;
                const roles = (w.graph?.nodes ?? [])
                  .filter((n) => n.kind === "role")
                  .map((n) => n.ref_id);
                return (
                  <button
                    key={w.workflow_id}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    onClick={() => setWorkflowId(on ? "" : w.workflow_id)}
                    className={`flex cursor-pointer flex-col gap-1.5 rounded-xl border bg-card p-3 text-left shadow-sm transition-colors hover:border-primary/40 hover:bg-accent/40 ${
                      on ? "border-primary ring-2 ring-primary/25" : ""
                    }`}
                  >
                    <span className="flex items-center gap-2">
                      <span className="truncate font-mono text-[13px] font-medium">{w.name}</span>
                      {!w.is_published ? (
                        <span className="rounded border px-1 py-0.5 text-[10px] text-muted-foreground">
                          draft
                        </span>
                      ) : null}
                    </span>
                    {w.description && (
                      <span className="line-clamp-2 text-xs text-muted-foreground">
                        {w.description}
                      </span>
                    )}
                    <span className="mt-auto flex flex-wrap items-center gap-1 pt-1 text-[11px] text-muted-foreground">
                      <span className="org-agent-tag">{count(w, "role")} roles</span>
                      <span className="org-agent-tag">{count(w, "skill")} skills</span>
                      {count(w, "tool") > 0 && (
                        <span className="org-agent-tag">{count(w, "tool")} tools</span>
                      )}
                      <span className="ml-auto">
                        {w.seed_inputs.length ? `${w.seed_inputs.length} input(s)` : "no input"}
                      </span>
                    </span>
                    {roles.length > 0 && (
                      <span className="truncate text-[10.5px] text-muted-foreground">
                        {roles.slice(0, 4).join(" → ")}
                        {roles.length > 4 ? " …" : ""}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
            {/* Real named field so the form's existing FormData submit picks it up. */}
            <input type="hidden" name="workflow_id" value={workflowId} />
          </div>

          {selected && (
            <div className="space-y-3">
              {selected.description && (
                <p className="text-sm text-muted-foreground">{selected.description}</p>
              )}
              {selected.seed_inputs.map((seed) => (
                <div key={seed.key} className="space-y-1.5">
                  <Label htmlFor={`${ids}-${seed.key}`}>
                    {seed.label || seed.key}
                    {seed.required && <span className="text-destructive"> *</span>}
                  </Label>
                  <Input
                    id={`${ids}-${seed.key}`}
                    name={`workflow_input__${seed.key}`}
                    required={seed.required}
                  />
                </div>
              ))}
              {selected.seed_inputs.length === 0 && (
                <p className="text-sm text-muted-foreground">
                  This workflow needs no extra input — just create the project.
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
