import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";

type Proposal = {
  id: string;
  kind: string;
  action: "create" | "update";
  target_id: string;
  title: string;
  rationale: string;
};

type Props = { listUrl: string; decideUrl: string; kind: "role" | "skill" | "mcp_binding" };

const APPROVED_MESSAGE: Record<Props["kind"], string> = {
  role: "Role created",
  skill: "Skill created",
  mcp_binding: "Binding applied",
};

/**
 * Pending role/skill proposals (Dream-suggested or marketplace-discovered),
 * and pending mcp_binding proposals (an MCP install whose role auto-bind
 * scored every role below threshold — see PlatformCatalog._stage_binding_review),
 * shown as cards in the same section as the real ones — tagged "Pending",
 * never assignable until approved here. Shares the ["catalog-proposals"]
 * query with ProposalBadge/MarketplacePanel (one QueryClient per page), so
 * deciding anywhere keeps every view in sync.
 */
export default function PendingProposals({ listUrl, decideUrl, kind }: Props) {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["catalog-proposals"],
    queryFn: () => api<{ proposals: Proposal[] }>(`${listUrl}?status=pending`).then((r) => r.proposals),
    refetchInterval: 30_000,
  });
  const proposals = (data ?? []).filter((p) => p.kind === kind);

  const decide = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: "approve" | "reject" }) =>
      api<{ ok: boolean; error?: string }>(decideUrl, { json: { id, decision } }),
    onSuccess: (res, { decision }) => {
      if (!res.ok) {
        toast.error(res.error || "Could not apply the proposal");
        return;
      }
      if (decision === "approve") {
        toast.success(APPROVED_MESSAGE[kind]);
        // The new row is server-rendered; reload so it shows up in the grid.
        window.setTimeout(() => window.location.reload(), 400);
        return;
      }
      toast.success("Proposal rejected");
      qc.invalidateQueries({ queryKey: ["catalog-proposals"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  if (!proposals.length) return null;

  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {proposals.map((p) => (
        <article key={p.id} className="flex flex-col gap-3 rounded-xl border border-dashed bg-card p-4 shadow-sm">
          <div className="flex items-start gap-2">
            <h3 className="min-w-0 flex-1 truncate font-mono text-[13px] font-medium">
              {p.target_id || p.title}
            </h3>
            <Badge variant="secondary">Pending</Badge>
          </div>
          <p className="line-clamp-4 text-sm text-muted-foreground">{p.rationale || p.title}</p>
          <div className="mt-auto flex justify-end gap-2">
            <Button
              size="sm"
              variant="ghost"
              disabled={decide.isPending}
              onClick={() => decide.mutate({ id: p.id, decision: "reject" })}
            >
              Reject
            </Button>
            <Button
              size="sm"
              disabled={decide.isPending}
              onClick={() => decide.mutate({ id: p.id, decision: "approve" })}
            >
              {decide.isPending ? <Loader2 className="animate-spin" /> : null}
              Approve
            </Button>
          </div>
        </article>
      ))}
    </div>
  );
}
