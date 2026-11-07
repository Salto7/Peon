import { ArrowDownToLine, ArrowUpToLine, FileUp, Plus, Trash2 } from "lucide-react";
import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { csrfToken } from "@/lib/api";

type Target = { type?: string; value?: string } | string;

type Props = {
  actionUrl: string;
  seed?: Target[] | null;
  inScope?: Target[] | null;
  exclusions?: Target[] | null;
  candidates?: Target[] | null;
  authorization?: string | null;
};

type LineItem = { id: string; text: string };
type Bucket = "in_scope" | "exclusions" | "candidates" | "seed";

const uid = () => Math.random().toString(36).slice(2, 10);

function formatTarget(t: Target): string {
  if (typeof t === "string") return t.trim();
  const value = String(t?.value ?? "").trim();
  if (!value) return "";
  const typ = String(t?.type ?? "").trim();
  return typ && typ !== "other" ? `${typ}:${value}` : value;
}

function toItems(targets: Target[] | null | undefined): LineItem[] {
  if (!targets?.length) return [];
  return targets
    .map((t) => formatTarget(t))
    .filter(Boolean)
    .map((text) => ({ id: uid(), text }));
}

function linesOf(items: LineItem[]): string {
  return items
    .map((i) => i.text.trim())
    .filter(Boolean)
    .join("\n");
}

type BucketMeta = {
  id: Bucket;
  title: string;
  description: string;
  name: string;
  empty: string;
  placeholder: string;
  tone: string;
  chip: string;
  locked?: boolean;
  allowAdd?: boolean;
};

const BUCKETS: BucketMeta[] = [
  {
    id: "in_scope",
    title: "In scope",
    description: "Authorized — demote to candidates or move to exclusions.",
    name: "in_scope",
    empty: "Nothing authorized yet.",
    placeholder: "domain:app.lab",
    tone: "border-success/40 bg-success/5",
    chip: "bg-success text-background",
    allowAdd: true,
  },
  {
    id: "exclusions",
    title: "Exclusions",
    description: "Deny-list carve-outs — still blocked by assert_in_scope.",
    name: "exclusions",
    empty: "No exclusions.",
    placeholder: "domain:admin.corp.com",
    tone: "border-destructive/40 bg-destructive/5",
    chip: "bg-destructive text-background",
    allowAdd: true,
  },
  {
    id: "candidates",
    title: "Candidates",
    description: "Discovered or not authorized — promote to in-scope or remove.",
    name: "candidates",
    empty: "No candidates yet.",
    placeholder: "domain:other.lab",
    tone: "border-warning/40 bg-warning/5",
    chip: "bg-warning text-background",
    allowAdd: true,
  },
  {
    id: "seed",
    title: "Seed",
    description: "Intent clues — cannot be removed (edit or add only).",
    name: "seed",
    empty: "No seed entries.",
    placeholder: "person:Alice",
    tone: "border-info/40 bg-info/5",
    chip: "bg-info text-background",
    locked: true,
    allowAdd: true,
  },
];

export default function RoeEditor({
  actionUrl,
  seed,
  inScope,
  exclusions,
  candidates,
  authorization,
}: Props) {
  // Separate deny-list — never fold exclusions into candidates (scope bug).
  const [scopeItems, setScopeItems] = useState(() => toItems(inScope));
  const [exclItems, setExclItems] = useState(() => toItems(exclusions));
  const [candItems, setCandItems] = useState(() => toItems(candidates));
  const [seedItems, setSeedItems] = useState(() => toItems(seed));
  const [authDoc, setAuthDoc] = useState(authorization?.trim() || "");
  const [drafts, setDrafts] = useState<Partial<Record<Bucket, string>>>({});
  const [pickedName, setPickedName] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const bags: Record<Bucket, { items: LineItem[]; set: (n: LineItem[]) => void }> = {
    in_scope: { items: scopeItems, set: setScopeItems },
    exclusions: { items: exclItems, set: setExclItems },
    candidates: { items: candItems, set: setCandItems },
    seed: { items: seedItems, set: setSeedItems },
  };

  const move = (from: Bucket, to: Bucket, item: LineItem) => {
    bags[from].set(bags[from].items.filter((row) => row.id !== item.id));
    bags[to].set([...bags[to].items, item]);
  };

  const remove = (from: Bucket, item: LineItem) => {
    if (from === "seed") return;
    bags[from].set(bags[from].items.filter((row) => row.id !== item.id));
  };

  const add = (bucket: Bucket) => {
    const text = (drafts[bucket] || "").trim();
    if (!text) return;
    bags[bucket].set([...bags[bucket].items, { id: uid(), text }]);
    setDrafts((d) => ({ ...d, [bucket]: "" }));
  };

  return (
    <form method="post" action={actionUrl} encType="multipart/form-data" className="space-y-5">
      <input type="hidden" name="csrfmiddlewaretoken" value={csrfToken()} />
      <input type="hidden" name="roe_action" value="save" />
      <input type="hidden" name="authorization" value={authDoc} />

      <section className="space-y-3">
        <header className="space-y-1">
          <h3 className="text-sm font-semibold tracking-tight">Scope</h3>
          <p className="text-xs text-muted-foreground">
            Green = authorized · Red = excluded · Yellow = candidates · Blue = seed (locked).
          </p>
        </header>
        <div className="space-y-3">
          {BUCKETS.map((meta) => {
            const { items } = bags[meta.id];
            return (
              <div key={meta.id} className={cn("space-y-3 rounded-lg border p-3", meta.tone)}>
                <input type="hidden" name={meta.name} value={linesOf(items)} />
                <header>
                  <div className="flex items-center gap-2">
                    <span className={cn("size-2 shrink-0 rounded-full", meta.chip)} aria-hidden />
                    <h4 className="text-sm font-semibold tracking-tight">{meta.title}</h4>
                    <span className="rounded-md border bg-background/60 px-1.5 py-0.5 text-[11px] text-muted-foreground">
                      {items.length}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">{meta.description}</p>
                </header>
                <ul className="space-y-2">
                  {items.map((item) => (
                    <li
                      key={item.id}
                      className="flex items-center gap-1.5 rounded-md border border-border/60 bg-background px-2 py-1.5"
                    >
                      <input
                        className="ui-input h-8 flex-1 font-mono text-xs"
                        value={item.text}
                        onChange={(e) =>
                          bags[meta.id].set(
                            items.map((row) =>
                              row.id === item.id ? { ...row, text: e.target.value } : row,
                            ),
                          )
                        }
                      />
                      <span className="flex shrink-0 items-center gap-0.5">
                        {meta.id === "candidates" ? (
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon-sm"
                            title="Promote to in-scope"
                            aria-label="Promote to in-scope"
                            onClick={() => move("candidates", "in_scope", item)}
                          >
                            <ArrowUpToLine className="size-3.5 text-success" />
                          </Button>
                        ) : null}
                        {meta.id === "in_scope" ? (
                          <>
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon-sm"
                              title="Demote to candidates"
                              aria-label="Demote to candidates"
                              onClick={() => move("in_scope", "candidates", item)}
                            >
                              <ArrowDownToLine className="size-3.5 text-warning" />
                            </Button>
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon-sm"
                              title="Move to exclusions"
                              aria-label="Move to exclusions"
                              onClick={() => move("in_scope", "exclusions", item)}
                            >
                              <ArrowDownToLine className="size-3.5 text-destructive" />
                            </Button>
                          </>
                        ) : null}
                        {meta.id === "exclusions" ? (
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon-sm"
                            title="Restore to candidates"
                            aria-label="Restore to candidates"
                            onClick={() => move("exclusions", "candidates", item)}
                          >
                            <ArrowUpToLine className="size-3.5 text-warning" />
                          </Button>
                        ) : null}
                        {!meta.locked ? (
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon-sm"
                            title="Remove"
                            aria-label="Remove"
                            onClick={() => remove(meta.id, item)}
                          >
                            <Trash2 className="size-3.5" />
                          </Button>
                        ) : null}
                      </span>
                    </li>
                  ))}
                  {!items.length ? (
                    <li className="rounded-md border border-dashed px-3 py-4 text-center text-xs text-muted-foreground">
                      {meta.empty}
                    </li>
                  ) : null}
                </ul>
                {meta.allowAdd ? (
                  <div className="flex gap-2">
                    <input
                      className="ui-input flex-1 font-mono text-xs"
                      placeholder={meta.placeholder}
                      value={drafts[meta.id] || ""}
                      onChange={(e) => setDrafts((d) => ({ ...d, [meta.id]: e.target.value }))}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          add(meta.id);
                        }
                      }}
                    />
                    <Button type="button" variant="outline" size="sm" onClick={() => add(meta.id)}>
                      <Plus className="size-3.5" /> Add
                    </Button>
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      </section>

      <section className="space-y-3 rounded-lg border border-border/80 bg-muted/20 p-4">
        <header className="space-y-1 border-b border-border/60 pb-3">
          <h3 className="text-sm font-semibold tracking-tight">Authorization document</h3>
          <p className="text-xs text-muted-foreground">
            Optional. Upload the letter of authorization or RoE PDF — no free-text fields.
          </p>
        </header>
        <div className="space-y-2">
          {authDoc ? (
            <p className="rounded-md border bg-background px-3 py-2 font-mono text-xs">
              Current: {authDoc}
            </p>
          ) : (
            <p className="text-xs text-muted-foreground">No document on file.</p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => fileRef.current?.click()}
            >
              <FileUp className="size-3.5" /> {authDoc ? "Replace document" : "Upload document"}
            </Button>
            <input
              ref={fileRef}
              type="file"
              name="authorization_doc"
              className="hidden"
              accept=".pdf,.doc,.docx,.txt,.md,.png,.jpg,.jpeg"
              onChange={(e) => {
                const f = e.target.files?.[0];
                setPickedName(f?.name || "");
              }}
            />
            {pickedName ? (
              <span className="text-xs text-muted-foreground">Selected: {pickedName}</span>
            ) : null}
            {authDoc ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => {
                  setAuthDoc("");
                  setPickedName("");
                  if (fileRef.current) fileRef.current.value = "";
                }}
              >
                Clear
              </Button>
            ) : null}
          </div>
        </div>
      </section>

      <div className="flex justify-end border-t pt-4">
        <Button type="submit">Save rules</Button>
      </div>
    </form>
  );
}
