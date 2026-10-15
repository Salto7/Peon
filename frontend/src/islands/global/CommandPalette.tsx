import { useQuery } from "@tanstack/react-query";
import { useDeferredValue, useEffect, useMemo, useState } from "react";
import { z } from "zod";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { api } from "@/lib/api";

const SearchItem = z.object({
  id: z.string(),
  label: z.string(),
  group: z.string().default("Results"),
  href: z.string(),
  meta: z.string().optional().nullable(),
  keywords: z.string().optional().nullable(),
});
const SearchResponse = z.object({ items: z.array(SearchItem) });
type SearchItem = z.infer<typeof SearchItem>;

function isTypingTarget(t: EventTarget | null) {
  const el = t as HTMLElement | null;
  return !!el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
}

/** ⌘K / Ctrl+K / ⇧S command palette backed by ``search.json``. */
export default function CommandPalette({ searchUrl }: { searchUrl: string }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const query = useDeferredValue(q.trim());

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const k = e.key.toLowerCase();
      if ((k === "k" && (e.metaKey || e.ctrlKey)) || (k === "s" && e.shiftKey && !isTypingTarget(e.target))) {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    const onClick = (e: MouseEvent) => {
      if ((e.target as HTMLElement).closest("[data-palette-open]")) {
        e.preventDefault();
        setOpen(true);
      }
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("click", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("click", onClick);
    };
  }, []);

  const { data, isFetching } = useQuery({
    queryKey: ["search", query],
    queryFn: ({ signal }) =>
      api(`${searchUrl}?q=${encodeURIComponent(query)}`, { schema: SearchResponse, signal }),
    enabled: open,
    placeholderData: (prev) => prev,
  });

  const groups = useMemo(() => {
    const out = new Map<string, SearchItem[]>();
    for (const item of data?.items ?? []) {
      const list = out.get(item.group) ?? [];
      list.push(item);
      out.set(item.group, list);
    }
    return [...out.entries()];
  }, [data]);

  return (
    <CommandDialog
      open={open}
      onOpenChange={setOpen}
      title="Search"
      description="Search projects, roles, tools and findings"
      shouldFilter={false}
    >
      <CommandInput
        value={q}
        onValueChange={setQ}
        placeholder="Search projects, roles, tools, findings…"
      />
      <CommandList>
        <CommandEmpty>{isFetching ? "Searching…" : "No results."}</CommandEmpty>
        {groups.map(([group, items]) => (
          <CommandGroup key={group} heading={group}>
            {items.map((item) => (
              <CommandItem
                key={item.id}
                value={item.id}
                onSelect={() => {
                  setOpen(false);
                  window.location.href = item.href;
                }}
              >
                <span className="truncate">{item.label}</span>
                {item.meta ? (
                  <span className="ml-auto truncate text-xs text-muted-foreground">{item.meta}</span>
                ) : null}
              </CommandItem>
            ))}
          </CommandGroup>
        ))}
      </CommandList>
    </CommandDialog>
  );
}
