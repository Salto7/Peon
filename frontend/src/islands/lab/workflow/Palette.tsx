import { Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Input } from "@/components/ui/input";
import type { Catalog, CatalogOption, NodeKind } from "./types";

type Props = {
  catalog: Catalog | undefined;
  /** kinds the current selection can take as a child (cards that would auto-link are highlighted) */
  linkable: ReadonlySet<NodeKind>;
  usedRefs: ReadonlySet<string>;
  onAdd: (kind: NodeKind, refId: string) => void;
};

const TABS: { kind: NodeKind; label: string; glyph: string }[] = [
  { kind: "role", label: "Roles", glyph: "♞" },
  { kind: "skill", label: "Skills", glyph: "♙" },
  { kind: "tool", label: "Tools", glyph: "⚒" },
];

/** Fixed chip size — does not stretch when the catalog panel is resized. */
const CHIP = "w-[11.5rem]";

export default function Palette({ catalog, linkable, usedRefs, onAdd }: Props) {
  const [kind, setKind] = useState<NodeKind>("role");
  const [query, setQuery] = useState("");
  const items = useMemo((): CatalogOption[] => {
    const all = kind === "role" ? catalog?.roles : kind === "skill" ? catalog?.skills : catalog?.tools;
    const q = query.trim().toLowerCase();
    return (all ?? []).filter((o) => !q || `${o.id} ${o.description}`.toLowerCase().includes(q));
  }, [catalog, kind, query]);

  return (
    <div className="flex h-full min-h-0 w-full flex-col gap-1.5 p-2">
      <div role="tablist" aria-label="Catalog" className="inline-flex w-full shrink-0 rounded-md border bg-muted p-0.5">
        {TABS.map((t) => (
          <button
            key={t.kind}
            type="button"
            role="tab"
            aria-selected={kind === t.kind}
            onClick={() => setKind(t.kind)}
            className="h-7 min-w-0 flex-1 cursor-pointer truncate rounded px-1 text-xs font-medium text-muted-foreground aria-selected:bg-background aria-selected:text-foreground aria-selected:shadow-sm"
          >
            <span aria-hidden="true">{t.glyph}</span> {t.label}
          </button>
        ))}
      </div>
      <div className="relative w-full shrink-0">
        <Search className="pointer-events-none absolute top-2 left-2 size-3 text-muted-foreground" />
        <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={`Search ${kind}s…`} className="h-8 w-full pl-7 text-xs" />
      </div>
      <ul className="flex min-h-0 w-full flex-1 flex-wrap content-start gap-1.5 overflow-y-auto overflow-x-hidden pr-0.5">
        {items.map((o) => {
          const used = usedRefs.has(`${kind}:${o.id}`);
          return (
            <li key={o.id} className={`shrink-0 ${CHIP}`}>
              <button
                type="button"
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.setData("application/x-workflow-item", JSON.stringify({ kind, refId: o.id }));
                  e.dataTransfer.effectAllowed = "copy";
                }}
                onClick={() => onAdd(kind, o.id)}
                title={
                  o.description
                    ? `${o.id} — ${o.description}`
                    : linkable.has(kind)
                      ? "Click: add and link under the selected card"
                      : "Click: add to the canvas"
                }
                className={`wf-card box-border ${CHIP} max-w-[11.5rem] cursor-grab rounded-md border bg-card px-2.5 py-2 text-left shadow-sm transition-colors hover:border-primary/40 hover:bg-accent/40 active:cursor-grabbing${
                  linkable.has(kind) ? " is-linkable" : ""
                }`}
              >
                <span className="flex min-w-0 items-center gap-1.5">
                  <span className="truncate font-mono text-xs font-medium leading-snug">{o.id}</span>
                  {kind === "role" && o.role_kind && o.role_kind !== "specialist" && (
                    <span className="org-agent-tag shrink-0 text-[10px]">{o.role_kind}</span>
                  )}
                  {used && <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">on</span>}
                </span>
                {o.description && (
                  <span className="mt-1 line-clamp-2 block text-[11px] leading-snug text-muted-foreground">{o.description}</span>
                )}
              </button>
            </li>
          );
        })}
        {items.length === 0 && (
          <li className="w-full px-1 py-4 text-center text-xs text-muted-foreground">Nothing matches.</li>
        )}
      </ul>
    </div>
  );
}
