import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";

type MemoryEntry = {
  id: string;
  scope: "project" | "platform" | "cognee";
  kind: string;
  name: string;
  description: string;
  cited: boolean;
  evidence: string[];
};

type StreamMsg = {
  message_type: string;
  metadata: { entries?: MemoryEntry[] };
};

type Props = { messagesUrl: string };

const SCOPE_LABEL: Record<MemoryEntry["scope"], string> = {
  project: "This engagement",
  platform: "Platform lesson",
  cognee: "Cross-project recall",
};

/**
 * UI-9: what memory this agent actually started knowing, exactly as injected
 * into its prompt (peon/projects/memory_store.py:index_block_detail, recorded
 * verbatim as a "memory"-kind StreamMessage by job_run.py so it can't drift
 * from what the agent really saw, even after the underlying entries change).
 * Every entry is a hint to weigh against this engagement's own evidence, not
 * a fact — "Cited" only means a Finding/Evidence from this same project backs
 * it, not that it is correct here.
 */
export default function MemoryTransparencyPanel({ messagesUrl }: Props) {
  const { data, isLoading } = useQuery({
    queryKey: ["job-memory-message", messagesUrl],
    queryFn: () => api<{ messages: StreamMsg[] }>(messagesUrl).then((r) => r.messages),
    refetchInterval: 15_000,
  });
  const entries = (data ?? []).find((m) => m.message_type === "memory")?.metadata.entries ?? [];

  if (isLoading) return null;
  if (!entries.length) {
    return (
      <p className="text-sm text-muted-foreground">
        No memory was recalled for this agent — either nothing durable is on file yet, or the job
        hasn't started.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-muted-foreground">
        Carried-over notes this agent started with — point-in-time, not instructions. Weigh each
        against what this engagement actually shows.
      </p>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {entries.map((e) => (
          <article
            key={e.id || e.name}
            className="flex flex-col gap-2 rounded-xl border bg-card p-4"
          >
            <div className="flex items-start justify-between gap-2">
              <h3 className="min-w-0 flex-1 truncate text-sm font-medium">{e.name}</h3>
              <Badge variant={e.cited ? "default" : "secondary"}>
                {e.cited ? "Cited" : "Uncited"}
              </Badge>
            </div>
            <p className="text-xs text-muted-foreground">
              {SCOPE_LABEL[e.scope]} · {e.kind}
            </p>
            <p className="line-clamp-4 text-sm">{e.description}</p>
            {}
            {(e.evidence ?? []).length ? (
              <ul className="mt-1 space-y-1 border-t pt-2 text-xs text-muted-foreground">
                {(e.evidence ?? []).map((cite) => (
                  <li key={cite} className="truncate font-mono">
                    {cite}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-1 border-t pt-2 text-xs text-muted-foreground">
                No Finding/Evidence from this engagement backs this — unverified.
              </p>
            )}
          </article>
        ))}
      </div>
    </div>
  );
}
