import { useMutation } from "@tanstack/react-query";
import { Loader2, Plus, Trash2 } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { toast } from "sonner";
import { CodeEditor } from "@/components/code-editor-lazy";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { requestConfirm } from "@/lib/behaviors";

type Kind = "role" | "skill" | "mcp" | "tool";
type Binding = { role_id: string; score: number; lead: boolean };

type Urls = {
  skill_source: string;
  skill_delete: string;
  tool_source: string;
  mcp_source: string;
  role_write: string;
  role_create: string;
};

type Draft = {
  kind: Kind;
  id: string;
  isNew: boolean;
  path: string;
  main: string;
  script: string;
  files: Record<string, string>;
  bindings: Binding[];
  role: { id: string; kind: string; aliases: string; goal: string; backstory: string };
};

type SourcePayload = {
  path?: string;
  skill_md?: string;
  document?: unknown;
  yaml?: string;
  install_script?: string;
  files?: Record<string, string>;
  bindings?: Partial<Binding>[];
  id?: string;
  kind?: string;
  aliases?: string[];
  goal?: string;
  backstory?: string;
};

const NEW = "__new__";
const fill = (tpl: string, id: string) => tpl.replace("{id}", encodeURIComponent(id));

function sourceUrl(urls: Urls, kind: Kind, id: string) {
  return {
    skill: fill(urls.skill_source, id),
    tool: fill(urls.tool_source, id),
    mcp: fill(urls.mcp_source, id),
    role: fill(urls.role_write, id),
  }[kind];
}

function deleteUrl(urls: Urls, kind: Kind, id: string) {
  return {
    skill: fill(urls.skill_delete, id),
    tool: fill(urls.tool_source, id),
    mcp: fill(urls.mcp_source, id),
    role: fill(urls.role_write, id),
  }[kind];
}

function toDraft(kind: Kind, id: string, d: SourcePayload): Draft {
  return {
    kind,
    id,
    isNew: id === NEW,
    path: d.path ?? "",
    main:
      kind === "skill"
        ? d.skill_md || "---\nname: \ndescription: \n---\n\n"
        : kind === "mcp"
          ? JSON.stringify(d.document ?? {}, null, 2)
          : (d.yaml ?? ""),
    script: d.install_script ?? "",
    files: d.files ?? {},
    bindings: (d.bindings ?? []).map((b) => ({
      role_id: String(b.role_id ?? ""),
      score: Number.isFinite(Number(b.score)) ? Number(b.score) : 1,
      lead: !!b.lead,
    })),
    role: {
      id: d.id ?? "",
      kind: d.kind ?? "technical",
      aliases: (d.aliases ?? []).join(", "),
      goal: d.goal ?? "",
      backstory: d.backstory ?? "",
    },
  };
}

const TITLES: Record<Kind, string> = { role: "role", skill: "skill", mcp: "MCP server", tool: "tool" };

export default function CatalogEditor({
  urls,
  roleKinds = ["technical", "engagement-pm", "analyzer"],
  roleNames = [],
}: {
  urls: Urls;
  roleKinds?: string[];
  roleNames?: string[];
}) {
  const ids = useId();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState("");
  const set = (patch: Partial<Draft>) => setDraft((d) => (d ? { ...d, ...patch } : d));
  const setRole = (patch: Partial<Draft["role"]>) =>
    setDraft((d) => (d ? { ...d, role: { ...d.role, ...patch } } : d));

  const load = useMutation({
    mutationFn: async ({ kind, id }: { kind: Kind; id: string }) => {
      if (id === NEW && (kind === "skill" || kind === "role")) return toDraft(kind, id, {});
      return toDraft(kind, id, await api<SourcePayload>(sourceUrl(urls, kind, id)));
    },
    onSuccess: (d) => {
      setError("");
      setDraft(d);
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const remove = useMutation({
    mutationFn: ({ kind, id }: { kind: Kind; id: string }) =>
      api(deleteUrl(urls, kind, id), { method: "DELETE", json: {} }),
    onSuccess: () => {
      toast.success("Deleted");
      window.setTimeout(() => window.location.reload(), 400);
    },
    onError: (err: Error) => toast.error(err.message),
  });

  // Bulk delete across sections/kinds. Keyed "kind:id" so selection survives
  // across roles/skills/mcp/tools without a Set<object> comparison headache.
  const [selected, setSelected] = useState<Map<string, { kind: Kind; id: string }>>(new Map());
  const bulkRemove = useMutation({
    mutationFn: async (items: { kind: Kind; id: string }[]) => {
      const results = await Promise.allSettled(
        items.map((item) => api(deleteUrl(urls, item.kind, item.id), { method: "DELETE", json: {} })),
      );
      const failed = results.filter((r) => r.status === "rejected").length;
      if (failed) throw new Error(`${failed} of ${items.length} failed to delete`);
    },
    onSuccess: () => {
      toast.success("Deleted selected items");
      window.setTimeout(() => window.location.reload(), 400);
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const loadItem = load.mutate;
  const removeItem = remove.mutate;
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      const el = (e.target as HTMLElement).closest<HTMLElement>("[data-catalog-edit],[data-catalog-delete]");
      if (!el) return;
      e.preventDefault();
      const id = el.dataset.id ?? "";
      if (el.dataset.catalogEdit) {
        loadItem({ kind: el.dataset.catalogEdit as Kind, id });
      } else {
        const kind = el.dataset.catalogDelete as Kind;
        requestConfirm({
          title: `Delete ${TITLES[kind]}?`,
          message: `“${id}” will be removed from the platform catalog.`,
          onConfirm: () => removeItem({ kind, id }),
        });
      }
    };
    const onChange = (e: Event) => {
      const el = e.target as HTMLInputElement;
      if (!el.matches?.("[data-catalog-select]")) return;
      const kind = el.dataset.catalogSelect as Kind;
      const id = el.dataset.id ?? "";
      const key = `${kind}:${id}`;
      setSelected((prev) => {
        const next = new Map(prev);
        if (el.checked) next.set(key, { kind, id });
        else next.delete(key);
        return next;
      });
    };
    document.addEventListener("click", onClick);
    document.addEventListener("change", onChange);
    return () => {
      document.removeEventListener("click", onClick);
      document.removeEventListener("change", onChange);
    };
  }, [loadItem, removeItem]);

  const selectedItems = [...selected.values()];
  const clearSelection = () => {
    setSelected(new Map());
    document
      .querySelectorAll<HTMLInputElement>("[data-catalog-select]:checked")
      .forEach((el) => {
        el.checked = false;
      });
  };

  const save = useMutation({
    mutationFn: async (d: Draft) => {
      const bindings = d.bindings.filter((b) => b.role_id.trim());
      if (d.kind === "skill") {
        return api(sourceUrl(urls, "skill", d.isNew ? NEW : d.id), {
          json: { skill_md: d.main, files: d.files, bindings },
        });
      }
      if (d.kind === "mcp") {
        let document: unknown;
        try {
          document = JSON.parse(d.main || "{}");
        } catch {
          throw new Error("Server JSON is invalid.");
        }
        return api(sourceUrl(urls, "mcp", d.isNew ? NEW : d.id), {
          json: { document, files: d.files, bindings },
        });
      }
      if (d.kind === "role") {
        return api(d.isNew ? urls.role_create : fill(urls.role_write, d.id), {
          json: {
            role_id: d.role.id.trim(),
            kind: d.role.kind,
            aliases: d.role.aliases.split(",").map((a) => a.trim()).filter(Boolean),
            goal: d.role.goal,
            backstory: d.role.backstory,
          },
        });
      }
      return api(sourceUrl(urls, "tool", d.id), { json: { yaml: d.main, install_script: d.script } });
    },
    onSuccess: () => {
      toast.success("Saved");
      setDraft(null);
      window.setTimeout(() => window.location.reload(), 400);
    },
    onError: (err: Error & { body?: { lint?: { errors?: string[] } } }) => {
      const msg = err.body?.lint?.errors?.join("; ") || err.message;
      setError(msg);
      toast.error(msg);
    },
  });

  const title = draft ? `${draft.isNew ? "New" : "Edit"} ${TITLES[draft.kind]}${draft.isNew ? "" : ` · ${draft.id}`}` : "";
  const mainLabel = draft?.kind === "skill" ? "SKILL.md" : draft?.kind === "mcp" ? "Server JSON" : "Catalog YAML";

  return (
    <>
      {load.isPending && (
        <div className="fixed right-4 bottom-4 z-[var(--z-toast)] flex items-center gap-2 rounded-md border bg-popover px-3 py-2 text-sm shadow-md">
          <Loader2 className="size-4 animate-spin" /> Loading…
        </div>
      )}
      {selectedItems.length > 0 && (
        <div className="fixed inset-x-0 bottom-4 z-[var(--z-toast)] mx-auto flex w-fit items-center gap-3 rounded-full border bg-popover px-4 py-2 text-sm shadow-lg">
          <span>
            {selectedItems.length} selected
          </span>
          <Button variant="ghost" size="sm" onClick={clearSelection}>
            Clear
          </Button>
          <Button
            variant="destructive"
            size="sm"
            disabled={bulkRemove.isPending}
            onClick={() =>
              requestConfirm({
                title: `Delete ${selectedItems.length} item(s)?`,
                message:
                  "These will be removed from the platform catalog: " +
                  selectedItems.map((i) => i.id).join(", "),
                onConfirm: () => bulkRemove.mutate(selectedItems),
              })
            }
          >
            {bulkRemove.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Trash2 className="size-4" />
            )}
            Delete selected
          </Button>
        </div>
      )}
      <Sheet open={!!draft} onOpenChange={(o) => !o && setDraft(null)}>
        <SheetContent className="w-full gap-0 sm:max-w-2xl">
          <SheetHeader className="border-b">
            <SheetTitle>{title}</SheetTitle>
            <SheetDescription>
              {draft?.kind === "mcp"
                ? "Env names only — set values under Project settings → MCP servers. Servers run inside the project sandbox (use host.docker.internal, not localhost)."
                : draft?.path || (draft?.isNew ? "Stored in the platform catalog." : "")}
            </SheetDescription>
          </SheetHeader>

          {draft && (
            <form
              id={`${ids}-form`}
              className="flex-1 space-y-5 overflow-y-auto p-4"
              onSubmit={(e) => {
                e.preventDefault();
                save.mutate(draft);
              }}
            >
              {error && (
                <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
                  {error}
                </p>
              )}

              {draft.kind === "role" ? (
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label htmlFor={`${ids}-rid`}>Role id</Label>
                    <Input
                      id={`${ids}-rid`}
                      required
                      pattern="[a-z0-9]+(-[a-z0-9]+)*"
                      readOnly={!draft.isNew}
                      value={draft.role.id}
                      onChange={(e) => setRole({ id: e.target.value })}
                      placeholder="web-expert"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor={`${ids}-rkind`}>Kind</Label>
                    <Select value={draft.role.kind} onValueChange={(v) => setRole({ kind: v })}>
                      <SelectTrigger id={`${ids}-rkind`} className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {roleKinds.map((k) => (
                          <SelectItem key={k} value={k}>
                            {k}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5 sm:col-span-2">
                    <Label htmlFor={`${ids}-ralias`}>Aliases</Label>
                    <Input
                      id={`${ids}-ralias`}
                      value={draft.role.aliases}
                      onChange={(e) => setRole({ aliases: e.target.value })}
                      placeholder="legacy-id, other-alias"
                    />
                  </div>
                  <div className="space-y-1.5 sm:col-span-2">
                    <Label htmlFor={`${ids}-rgoal`}>Goal</Label>
                    <Textarea id={`${ids}-rgoal`} rows={3} value={draft.role.goal} onChange={(e) => setRole({ goal: e.target.value })} />
                  </div>
                  <div className="space-y-1.5 sm:col-span-2">
                    <Label htmlFor={`${ids}-rstory`}>Backstory</Label>
                    <Textarea id={`${ids}-rstory`} rows={6} value={draft.role.backstory} onChange={(e) => setRole({ backstory: e.target.value })} />
                  </div>
                </div>
              ) : (
                <div className="space-y-1.5">
                  <Label htmlFor={`${ids}-main`}>{mainLabel}</Label>
                  <CodeEditor
                    id={`${ids}-main`}
                    aria-label={mainLabel}
                    language={draft.kind === "skill" ? "markdown" : draft.kind === "mcp" ? "json" : "yaml"}
                    value={draft.main}
                    onChange={(main) => set({ main })}
                    minHeight="16rem"
                  />
                </div>
              )}

              {draft.kind === "tool" && (
                <div className="space-y-1.5">
                  <Label htmlFor={`${ids}-script`}>Install script <span className="font-normal text-muted-foreground">(optional)</span></Label>
                  <CodeEditor id={`${ids}-script`} aria-label="Install script" language="shell" value={draft.script} onChange={(script) => set({ script })} />
                </div>
              )}

              {Object.keys(draft.files).map((rel) => (
                <div key={rel} className="space-y-1.5">
                  <Label className="font-mono text-xs">{rel}</Label>
                  <CodeEditor
                    aria-label={rel}
                    language={rel.endsWith(".md") ? "markdown" : rel.endsWith(".json") ? "json" : rel.match(/\.ya?ml$/) ? "yaml" : rel.endsWith(".sh") ? "shell" : "text"}
                    value={draft.files[rel] ?? ""}
                    onChange={(v) => set({ files: { ...draft.files, [rel]: v } })}
                  />
                </div>
              ))}

              {(draft.kind === "skill" || draft.kind === "mcp") && (
                <BindingsEditor
                  bindings={draft.bindings}
                  roleNames={roleNames}
                  onChange={(bindings) => set({ bindings })}
                />
              )}
            </form>
          )}

          <SheetFooter className="flex-row justify-end border-t">
            <Button variant="outline" onClick={() => setDraft(null)}>
              Cancel
            </Button>
            <Button type="submit" form={`${ids}-form`} disabled={save.isPending}>
              {save.isPending && <Loader2 className="animate-spin" />}
              Save
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>
    </>
  );
}

function BindingsEditor({
  bindings,
  roleNames,
  onChange,
}: {
  bindings: Binding[];
  roleNames: string[];
  onChange: (b: Binding[]) => void;
}) {
  const listId = useId();
  const update = (i: number, patch: Partial<Binding>) =>
    onChange(bindings.map((b, j) => (j === i ? { ...b, ...patch } : b)));
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium">Role mappings</legend>
      <p className="text-xs text-muted-foreground">Leave empty to auto-bind from the name and description.</p>
      <datalist id={listId}>
        {roleNames.map((r) => (
          <option key={r} value={r} />
        ))}
      </datalist>
      {bindings.length > 0 && (
        <div className="divide-y rounded-md border">
          <div className="grid grid-cols-[1fr_5rem_4rem_2rem] gap-2 px-3 py-2 text-xs text-muted-foreground">
            <span>Role</span>
            <span>Score</span>
            <span>Lead</span>
            <span className="sr-only">Remove</span>
          </div>
          {bindings.map((b, i) => (
            <div key={i} className="grid grid-cols-[1fr_5rem_4rem_2rem] items-center gap-2 px-3 py-2">
              <Input
                aria-label="Role"
                list={listId}
                value={b.role_id}
                onChange={(e) => update(i, { role_id: e.target.value })}
                className="h-8"
              />
              <Input
                aria-label="Score"
                type="number"
                step="0.1"
                value={b.score}
                onChange={(e) => update(i, { score: Number(e.target.value) })}
                className="h-8 tabular-nums"
              />
              <Switch aria-label="Lead" checked={b.lead} onCheckedChange={(lead) => update(i, { lead })} />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="size-8"
                aria-label="Remove mapping"
                onClick={() => onChange(bindings.filter((_, j) => j !== i))}
              >
                <Trash2 />
              </Button>
            </div>
          ))}
        </div>
      )}
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => onChange([...bindings, { role_id: "", score: 1, lead: false }])}
      >
        <Plus /> Add mapping
      </Button>
    </fieldset>
  );
}
