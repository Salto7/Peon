import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";

type Proposal = { kind: string; payload: { role?: { role_id?: string } } };

type Props = { listUrl: string; kind: string };

/**
 * "+N pending" next to a Catalog nav tab — stays visible until someone
 * actually approves/rejects, unlike a toast that can be dismissed unread.
 * Shares the ["catalog-proposals"] query with MarketplacePanel/ProposalsQueue
 * (one QueryClient per page), so acting anywhere refreshes this for free.
 * ``kind`` is comma-separated when a tab covers more than one proposal kind
 * (e.g. MCP servers: "mcp_binding,mcp_role_skill").
 */
export default function ProposalBadge({ listUrl, kind }: Props) {
  const { data } = useQuery({
    queryKey: ["catalog-proposals"],
    queryFn: () => api<{ proposals: Proposal[] }>(`${listUrl}?status=pending`).then((r) => r.proposals),
    refetchInterval: 30_000,
  });
  const kinds = new Set(kind.split(","));
  const count = (data ?? []).filter((p) => kinds.has(p.kind)).length;
  if (!count) return null;
  return (
    <Badge variant="secondary" className="ml-1">
      +{count} pending
    </Badge>
  );
}
