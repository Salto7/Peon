import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Loader2, Plus, Search, Trash2 } from "lucide-react";
import { useId, useMemo, useState } from "react";
import { toast } from "sonner";
import { ToneBadge, humanize, type Tone } from "@/components/status";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { requestConfirm } from "@/lib/behaviors";
import type { ProjectContext } from "./types";

type Entry = {
  name: string;
  kind: string;
  description: string;
  body: string;
  role_id: string;
  created_by: string;
  scope: "project" | "platform";
  recall_count: number;
  updated_at: string;
};
type Listing = { kinds: string[]; project: Entry[]; platform: Entry[] };

const KIND_TONE: Record<string, Tone> = {
  feedback: "primary",
  decision: "info",
  fact: "neutral",
  lesson: "success",
  reference: "neutral",
};

/** What the agents remember: engagement notes and platform lessons. */
export default function MemoryPanel({ ctx }: { ctx: ProjectContext }) {
  const qc = useQueryClient();
  const base = `/projects/${ctx.pk}`;
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["memory", ctx.pk],
    queryFn: ({ signal }) => api<Listing>(`${base}/memory.json`, { signal }),
    refetchInterval: 20_000,
  });

  const forget = useMutation({
    mutationFn: (e: Entry) =>
      api(`${base}/memory/`, { json: { action: "forget", scope: e.scope, name: e.name } }),
    onSuccess: () => {
      toast.success("Memory forgotten");
      qc.invalidateQueries({ queryKey: ["memory", ctx.pk] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const match = (e: Entry) => {
    const q = filter.trim().toLowerCase();
    return !q || `${e.name} ${e.description} ${e.body} ${e.kind}`.toLowerCase().includes(q);
  };
  const project = useMemo(() => (data?.project ?? []).filter(match), [data, filter]); // eslint-disable-line react-hooks/exhaustive-deps
  const platform = useMemo(() => (data?.platform ?? []).filter(match), [data, filter]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-56 flex-1">
          <Search className="pointer-events-none absolute top-2.5 left-2.5 size-4 text-muted-foreground" aria-hidden />
          <Input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter memories…"
            className="pl-8"
            aria-label="Filter memories"
          />
        </div>
        <Button onClick={() => setAdding(true)}>
          <Plus /> Add note
        </Button>
      </div>

      {isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : (
        <>
          <Section
            title="This engagement"
            hint="Operator feedback, facts, decisions and references for this project. Every agent sees the index at the start of its run."
            entries={project}
            open={open}
            setOpen={setOpen}
            onForget={(e) =>
              requestConfirm({
                title: "Forget this memory?",
                message: `“${e.name}” will leave every agent's index.`,
                confirmLabel: "Forget",
                onConfirm: () => forget.mutate(e),
              })
            }
            empty="Nothing remembered yet. Agents save durable discoveries here; your instructions are captured automatically."
          />
          <Section
            title="Platform lessons"
            hint="Generic lessons shared across engagements. Never contain targets, IPs, emails or URLs."
            entries={platform}
            open={open}
            setOpen={setOpen}
            onForget={(e) =>
              requestConfirm({
                title: "Forget this platform lesson?",
                message: `“${e.name}” is shared by every project.`,
                confirmLabel: "Forget",
                onConfirm: () => forget.mutate(e),
              })
            }
            empty="No platform lessons yet."
          />
        </>
      )}

      <AddDialog
        open={adding}
        onClose={() => setAdding(false)}
        kinds={data?.kinds ?? ["fact", "feedback", "decision", "lesson", "reference"]}
        pk={ctx.pk}
        onSaved={() => qc.invalidateQueries({ queryKey: ["memory", ctx.pk] })}
      />
    </div>
  );
}

function Section({
  title,
  hint,
  entries,
  open,
  setOpen,
  onForget,
  empty,
}: {
  title: string;
  hint: string;
  entries: Entry[];
  open: string | null;
  setOpen: (k: string | null) => void;
  onForget: (e: Entry) => void;
  empty: string;
}) {
  return (
    <section className="space-y-3">
      <div>
        <h2 className="flex items-center gap-2 text-base font-semibold">
          <Brain className="size-4 text-muted-foreground" aria-hidden /> {title}
          <span className="text-xs font-normal text-muted-foreground">{entries.length}</span>
        </h2>
        <p className="text-xs text-muted-foreground">{hint}</p>
      </div>
      {entries.length ? (
        <ul className="divide-y overflow-hidden rounded-xl border bg-card shadow-sm">
          {entries.map((e) => {
            const key = `${e.scope}:${e.name}`;
            const isOpen = open === key;
            return (
              <li key={key}>
                <div className="flex items-start gap-3 px-4 py-3">
                  <button
                    type="button"
                    className="min-w-0 flex-1 cursor-pointer text-left"
                    aria-expanded={isOpen}
                    onClick={() => setOpen(isOpen ? null : key)}
                  >
                    <span className="flex flex-wrap items-center gap-2">
                      <ToneBadge tone={KIND_TONE[e.kind] ?? "neutral"}>{humanize(e.kind)}</ToneBadge>
                      <span className="font-mono text-[13px] font-medium">{e.name}</span>
                    </span>
                    <span className="mt-1 block text-sm text-muted-foreground">{e.description}</span>
                  </button>
                  <div className="flex shrink-0 items-center gap-3 text-xs text-muted-foreground">
                    <span title="Times agents recalled this" className="tabular-nums">
                      {e.recall_count}×
                    </span>
                    <span className="hidden sm:inline">{e.created_by || "agent"}</span>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-8"
                      aria-label={`Forget ${e.name}`}
                      onClick={() => onForget(e)}
                    >
                      <Trash2 />
                    </Button>
                  </div>
                </div>
                {isOpen && (
                  <div className="border-t bg-muted/30 px-4 py-3 text-sm">
                    <p className="whitespace-pre-wrap">{e.body || "(no body)"}</p>
                    <p className="mt-2 text-xs text-muted-foreground">
                      Updated {new Date(e.updated_at).toLocaleString()}
                      {e.role_id ? ` · role ${e.role_id}` : ""}
                    </p>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">{empty}</p>
      )}
    </section>
  );
}

function AddDialog({
  open,
  onClose,
  kinds,
  pk,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  kinds: string[];
  pk: string;
  onSaved: () => void;
}) {
  const ids = useId();
  const [form, setForm] = useState({ name: "", kind: "fact", description: "", body: "", scope: "project" });
  const set = (patch: Partial<typeof form>) => setForm((f) => ({ ...f, ...patch }));
  const save = useMutation({
    mutationFn: () => api(`/projects/${pk}/memory/`, { json: { action: "save", ...form } }),
    onSuccess: () => {
      toast.success("Memory saved");
      onSaved();
      setForm({ name: "", kind: "fact", description: "", body: "", scope: "project" });
      onClose();
    },
    onError: (e: Error) => toast.error(e.message),
  });
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Add a note for the agents</DialogTitle>
          <DialogDescription>
            Agents see the one-line description in their index and can recall the full text.
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor={`${ids}-name`}>Name</Label>
              <Input id={`${ids}-name`} required value={form.name} onChange={(e) => set({ name: e.target.value })} placeholder="skip-cdn-hosts" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`${ids}-kind`}>Kind</Label>
              <Select value={form.kind} onValueChange={(kind) => set({ kind })}>
                <SelectTrigger id={`${ids}-kind`} className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {kinds.map((k) => (
                    <SelectItem key={k} value={k}>
                      {humanize(k)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`${ids}-desc`}>One-line description</Label>
            <Input id={`${ids}-desc`} required value={form.description} onChange={(e) => set({ description: e.target.value })} placeholder="Never scan CDN-fronted hosts" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`${ids}-body`}>Details</Label>
            <Textarea id={`${ids}-body`} rows={5} value={form.body} onChange={(e) => set({ body: e.target.value })} placeholder="Why this matters and how agents should apply it." />
          </div>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={save.isPending}>
              {save.isPending && <Loader2 className="animate-spin" />} Save
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
