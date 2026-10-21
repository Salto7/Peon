import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";

type Proposal = { kind: string; payload: { role?: { role_id?: string } } };

type Props = { listUrl: string; kind: "role" | "skill" | "mcp_binding" };

/**
 * "+N pending" next to a Catalog nav tab — stays visible until someone
 * actually approves/rejects, unlike a toast that can be dismissed unread.
 * Shares the ["catalog-proposals"] query with MarketplacePanel/ProposalsQueue
 * (one QueryClient per page), so acting anywhere refreshes this for free.
 */
export default function ProposalBadge({ listUrl, kind }: Props) {
  const { data } = useQuery({
    queryKey: ["catalog-proposals"],
    queryFn: () => api<{ proposals: Proposal[] }>(`${listUrl}?status=pending`).then((r) => r.proposals),
    refetchInterval: 30_000,
  });
  const count = (data ?? []).filter((p) => p.kind === kind).length;
  if (!count) return null;
  return (
    <Badge variant="secondary" className="ml-1">
      +{count} pending
    </Badge>
  );
}
