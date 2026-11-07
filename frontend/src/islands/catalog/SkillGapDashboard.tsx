import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

type Gap = {
  role_id: string;
  gap_kind: "mcp" | "binary";
  name: string;
  count: number;
  first_seen_at: string;
  last_seen_at: string;
};

type Props = { listUrl: string };

const KIND_LABEL: Record<Gap["gap_kind"], string> = {
  mcp: "MCP server",
  binary: "Binary",
};

/**
 * Read-only capability backlog (Core 5.3/UI-8): how often a role reached for
 * a binary or MCP server it has no binding for, so operators/platform
 * maintainers see demand pressure without reading logs. Nothing here is
 * actionable from this panel — fixing a gap means binding the role to the
 * capability on its Role or the MCP's binding list, elsewhere in this page.
 */
export default function SkillGapDashboard({ listUrl }: Props) {
  const [kind, setKind] = useState<"all" | Gap["gap_kind"]>("all");
  const { data, isLoading } = useQuery({
    queryKey: ["skill-gaps"],
    queryFn: () => api<{ gaps: Gap[] }>(listUrl).then((r) => r.gaps),
    refetchInterval: 60_000,
  });
  const gaps = (data ?? []).filter((g) => kind === "all" || g.gap_kind === kind);
  const total = gaps.reduce((n, g) => n + g.count, 0);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          {isLoading
            ? "Loading…"
            : gaps.length
              ? `${gaps.length} gap${gaps.length === 1 ? "" : "s"}, ${total} request${total === 1 ? "" : "s"} total.`
              : "No capability gaps recorded yet."}
        </p>
        <div className="flex gap-1">
          {(["all", "mcp", "binary"] as const).map((k) => (
            <Button
              key={k}
              size="sm"
              variant={kind === k ? "default" : "ghost"}
              onClick={() => setKind(k)}
            >
              {k === "all" ? "All" : KIND_LABEL[k]}
            </Button>
          ))}
        </div>
      </div>
      {gaps.length ? (
        <div className="ui-table-wrap max-h-[60vh] overflow-y-auto">
          <table className="ui-table">
            <thead>
              <tr>
                <th>Role</th>
                <th>Kind</th>
                <th>Requested</th>
                <th className="text-right">Times</th>
                <th>Last seen</th>
              </tr>
            </thead>
            <tbody>
              {gaps.map((g) => (
                <tr key={`${g.role_id}:${g.gap_kind}:${g.name}`}>
                  <td className="whitespace-nowrap font-mono text-xs">{g.role_id}</td>
                  <td>
                    <Badge variant="secondary">{KIND_LABEL[g.gap_kind]}</Badge>
                  </td>
                  <td className="font-mono text-xs">{g.name}</td>
                  <td
                    className={cn(
                      "text-right tabular-nums",
                      g.count >= 10 && "font-semibold text-foreground",
                    )}
                  >
                    {g.count}
                  </td>
                  <td className="whitespace-nowrap text-xs text-muted-foreground">
                    {g.last_seen_at ? new Date(g.last_seen_at).toLocaleString() : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
