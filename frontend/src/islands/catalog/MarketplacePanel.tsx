import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Loader2, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useId, useRef, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
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
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

type DiscoveredSkill = { name: string; namespace: string; description: string; path: string };
type DiscoveredAgent = { name: string; description: string; instructions: string; path: string };
type DiscoveredMcp = {
  name: string;
  repo_url: string;
  secrets: string[];
  configuration: string[];
  path: string;
};

type Source = {
  id: string;
  name: string;
  source_type: "github" | "zip";
  url: string;
  ref: string;
  subpath: string;
  status: "pending" | "scanning" | "scanned" | "error";
  error: string;
  discovered: { skills?: DiscoveredSkill[]; agents?: DiscoveredAgent[]; mcp_pointers?: DiscoveredMcp[] };
  last_scanned_at: string;
};

type Proposal = {
  id: string;
  kind: string;
  status: string;
  payload: { role?: { role_id?: string } };
};

type Urls = { delete: string; scan: string; import: string };
type ProposalUrls = { list: string; decide: string };

type Props = { listUrl: string; urls: Urls; proposalUrls: ProposalUrls; sources: Source[] };

const STATUS_VARIANT: Record<Source["status"], "secondary" | "outline" | "default" | "destructive"> = {
  pending: "secondary",
  scanning: "outline",
  scanned: "default",
  error: "destructive",
};

function withId(template: string, id: string): string {
  return template.replace("{id}", id);
}

/**
 * Register a GitHub repo or uploaded zip, scan it for skills/agents/MCP
 * pointers, and import reviewed items one at a time. Scanning only ever
 * reads files as text — nothing from a source is ever executed.
 */
export default function MarketplacePanel({ listUrl, urls, proposalUrls, sources: initial }: Props) {
  const ids = useId();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [sourceType, setSourceType] = useState<"github" | "zip">("github");
  const [url, setUrl] = useState("");
  const [ref, setRef] = useState("");
  const [subpath, setSubpath] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const { data } = useQuery({
    queryKey: ["marketplace-sources"],
    queryFn: () => api<{ sources: Source[] }>(listUrl).then((r) => r.sources),
    initialData: initial,
    refetchInterval: (query) =>
      (query.state.data ?? []).some((s) => s.status === "scanning") ? 2000 : false,
  });
  const sources = data ?? [];

  // Shared with ProposalBadge (same query key) so approving/rejecting here
  // updates the Catalog nav badge too, without a page reload.
  const { data: proposals } = useQuery({
    queryKey: ["catalog-proposals"],
    queryFn: () => api<{ proposals: Proposal[] }>(`${proposalUrls.list}?status=pending`).then((r) => r.proposals),
    refetchInterval: 30_000,
  });
  const pendingRoleByName = new Map(
    (proposals ?? [])
      .filter((p) => p.kind === "role" && p.payload.role?.role_id)
      .map((p) => [p.payload.role!.role_id as string, p]),
  );

  const invalidate = () => qc.invalidateQueries({ queryKey: ["marketplace-sources"] });
  const invalidateProposals = () => qc.invalidateQueries({ queryKey: ["catalog-proposals"] });

  const decide = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: "approve" | "reject" }) =>
      api<{ ok: boolean; error?: string }>(proposalUrls.decide, { json: { id, decision } }),
    onSuccess: (res, { decision }) => {
      if (!res.ok) {
        toast.error(res.error || "Could not apply the proposal");
        return;
      }
      if (decision === "approve") {
        toast.success("Role created");
        // The new role is server-rendered in Catalog → Roles; reload so it shows up there.
        window.setTimeout(() => window.location.reload(), 400);
        return;
      }
      toast.success("Proposal rejected");
      invalidateProposals();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const create = useMutation({
    mutationFn: () => {
      if (sourceType === "zip") {
        const file = fileInput.current?.files?.[0];
        if (!file) throw new Error("Choose a .zip file");
        const form = new FormData();
        form.set("name", name.trim());
        form.set("source_type", "zip");
        form.set("subpath", subpath.trim());
        form.set("file", file);
        return api<{ source: Source }>(listUrl, { form });
      }
      return api<{ source: Source }>(listUrl, {
        json: { name: name.trim(), source_type: "github", url: url.trim(), ref: ref.trim(), subpath: subpath.trim() },
      });
    },
    onSuccess: ({ source }) => {
      toast.success(`Added ${source.name}`);
      setName("");
      setUrl("");
      setRef("");
      setSubpath("");
      if (fileInput.current) fileInput.current.value = "";
      setOpen(source.id);
      invalidate();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const scan = useMutation({
    mutationFn: (id: string) => api(withId(urls.scan, id), { method: "POST" }),
    onSuccess: invalidate,
    onError: (e: Error) => toast.error(e.message),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api(withId(urls.delete, id), { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Source removed");
      invalidate();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const importItem = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) =>
      api<{ ok: boolean; kind?: string; id?: string; ids?: string[] }>(withId(urls.import, id), {
        json: body,
      }),
    onSuccess: (res) => {
      if (res.kind === "role_proposal") {
        toast.success("Staged as a role proposal — review it below or approve it right here.");
        invalidateProposals();
        return;
      }
      toast.success(
        res.ids ? `Installed: ${res.ids.join(", ")}` : res.id ? `Imported as ${res.id}` : "Imported",
      );
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <div className="space-y-4">
      <div className="space-y-3 rounded-lg border p-4">
        <div className="grid gap-3 sm:grid-cols-[10rem_1fr]">
          <div className="space-y-1.5">
            <Label htmlFor={`${ids}-type`}>Source</Label>
            <Select value={sourceType} onValueChange={(v) => setSourceType(v as "github" | "zip")}>
              <SelectTrigger id={`${ids}-type`} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="github">GitHub repo</SelectItem>
                <SelectItem value="zip">Upload zip</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={`${ids}-name`}>Name</Label>
            <Input id={`${ids}-name`} value={name} onChange={(e) => setName(e.target.value)} placeholder="SpecterOps skills" />
          </div>
        </div>
        {sourceType === "github" ? (
          <div className="grid gap-3 sm:grid-cols-[1fr_8rem_12rem]">
            <div className="space-y-1.5">
              <Label htmlFor={`${ids}-url`}>Repository URL</Label>
              <Input
                id={`${ids}-url`}
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://github.com/owner/repo"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`${ids}-ref`}>Branch/tag</Label>
              <Input id={`${ids}-ref`} value={ref} onChange={(e) => setRef(e.target.value)} placeholder="main" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`${ids}-subpath`}>Subpath (optional)</Label>
              <Input
                id={`${ids}-subpath`}
                value={subpath}
                onChange={(e) => setSubpath(e.target.value)}
                placeholder="plugins/bloodhound"
              />
            </div>
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-[1fr_12rem]">
            <div className="space-y-1.5">
              <Label htmlFor={`${ids}-file`}>Zip file</Label>
              <Input id={`${ids}-file`} ref={fileInput} type="file" accept=".zip" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`${ids}-subpath2`}>Subpath (optional)</Label>
              <Input
                id={`${ids}-subpath2`}
                value={subpath}
                onChange={(e) => setSubpath(e.target.value)}
                placeholder="plugins/bloodhound"
              />
            </div>
          </div>
        )}
        <div className="flex justify-end">
          <Button onClick={() => create.mutate()} disabled={create.isPending || !name.trim()}>
            {create.isPending ? <Loader2 className="animate-spin" /> : <Plus />}
            Add source
          </Button>
        </div>
      </div>

      {sources.length === 0 ? (
        <p className="px-1 text-sm text-muted-foreground">No marketplace sources registered yet.</p>
      ) : (
        <div className="space-y-3">
          {sources.map((source) => {
            const isOpen = open === source.id;
            const skills = source.discovered.skills ?? [];
            const agents = source.discovered.agents ?? [];
            const mcps = source.discovered.mcp_pointers ?? [];
            const total = skills.length + agents.length + mcps.length;
            return (
              <div key={source.id} className="rounded-lg border">
                <button
                  type="button"
                  className="flex w-full cursor-pointer items-center gap-3 px-4 py-3 text-left"
                  onClick={() => setOpen(isOpen ? null : source.id)}
                >
                  <ChevronRight className={cn("size-4 transition-transform", isOpen && "rotate-90")} />
                  <span className="min-w-0 flex-1 truncate text-sm font-medium">{source.name}</span>
                  <Badge variant={STATUS_VARIANT[source.status]}>{source.status}</Badge>
                  {source.status === "scanned" && <span className="text-xs text-muted-foreground">{total} found</span>}
                </button>
                {isOpen && (
                  <div className="space-y-4 border-t px-4 py-4">
                    <p className="font-mono text-xs text-muted-foreground">
                      {source.source_type === "github" ? `${source.url}${source.ref ? ` @ ${source.ref}` : ""}` : "uploaded zip"}
                      {source.subpath && ` — ${source.subpath}`}
                    </p>
                    {source.error && <p className="text-sm text-destructive">{source.error}</p>}
                    <div className="flex gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={scan.isPending || source.status === "scanning"}
                        onClick={() => scan.mutate(source.id)}
                      >
                        {source.status === "scanning" ? <Loader2 className="animate-spin" /> : <RefreshCw />}
                        {source.status === "pending" ? "Scan" : "Rescan"}
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-destructive hover:text-destructive"
                        disabled={remove.isPending}
                        onClick={() => remove.mutate(source.id)}
                      >
                        <Trash2 /> Remove
                      </Button>
                    </div>

                    {skills.length > 0 && (
                      <div className="space-y-2">
                        <h4 className="text-xs font-semibold text-muted-foreground">Skills ({skills.length})</h4>
                        {skills.map((skill) => (
                          <div key={skill.path} className="flex items-start justify-between gap-3 rounded-md border px-3 py-2">
                            <div className="min-w-0">
                              <p className="font-mono text-xs font-medium">
                                {skill.namespace ? `${skill.namespace}/` : ""}{skill.name}
                              </p>
                              <p className="text-xs text-muted-foreground">{skill.description || "No description."}</p>
                            </div>
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={importItem.isPending}
                              onClick={() => importItem.mutate({ id: source.id, body: { kind: "skill", path: skill.path } })}
                            >
                              Import
                            </Button>
                          </div>
                        ))}
                      </div>
                    )}

                    {agents.length > 0 && (
                      <div className="space-y-2">
                        <h4 className="text-xs font-semibold text-muted-foreground">
                          Agents — stage as role proposals ({agents.length})
                        </h4>
                        {agents.map((agent) => {
                          const proposal = pendingRoleByName.get(agent.name);
                          return (
                            <div key={agent.path} className="flex items-start justify-between gap-3 rounded-md border px-3 py-2">
                              <div className="min-w-0">
                                <p className="font-mono text-xs font-medium">{agent.name}</p>
                                <p className="line-clamp-2 text-xs text-muted-foreground">{agent.description || "No description."}</p>
                              </div>
                              {proposal ? (
                                <div className="flex shrink-0 items-center gap-2">
                                  <Badge variant="secondary">Pending review</Badge>
                                  <Button
                                    size="sm"
                                    variant="ghost"
                                    disabled={decide.isPending}
                                    onClick={() => decide.mutate({ id: proposal.id, decision: "reject" })}
                                  >
                                    Reject
                                  </Button>
                                  <Button
                                    size="sm"
                                    disabled={decide.isPending}
                                    onClick={() => decide.mutate({ id: proposal.id, decision: "approve" })}
                                  >
                                    Approve
                                  </Button>
                                </div>
                              ) : (
                                <Button
                                  size="sm"
                                  variant="outline"
                                  disabled={importItem.isPending}
                                  onClick={() =>
                                    importItem.mutate({
                                      id: source.id,
                                      body: {
                                        kind: "agent",
                                        name: agent.name,
                                        description: agent.description,
                                        instructions: agent.instructions,
                                      },
                                    })
                                  }
                                >
                                  Propose role
                                </Button>
                              )}
                            </div>
                          );
                        })}
                        <p className="text-xs text-muted-foreground">
                          Proposing stages a role for review — approve or reject it right here, or under Catalog → Roles.
                        </p>
                      </div>
                    )}

                    {mcps.length > 0 && (
                      <div className="space-y-2">
                        <h4 className="text-xs font-semibold text-muted-foreground">MCP servers ({mcps.length})</h4>
                        {mcps.map((mcp) => (
                          <div key={mcp.path} className="flex items-start justify-between gap-3 rounded-md border px-3 py-2">
                            <div className="min-w-0">
                              <p className="font-mono text-xs font-medium">{mcp.name}</p>
                              <p className="truncate font-mono text-xs text-muted-foreground">{mcp.repo_url}</p>
                              {(mcp.secrets.length > 0 || mcp.configuration.length > 0) && (
                                <p className="text-xs text-muted-foreground">
                                  Needs: {[...mcp.secrets, ...mcp.configuration].join(", ")}
                                </p>
                              )}
                            </div>
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={importItem.isPending}
                              onClick={() =>
                                importItem.mutate({ id: source.id, body: { kind: "mcp_pointer", repo_url: mcp.repo_url } })
                              }
                            >
                              Install server
                            </Button>
                          </div>
                        ))}
                        <p className="text-xs text-muted-foreground">
                          Installs the real server repo the pointer names, via the same git installer as "Install an MCP
                          server" above — set its env values under a project's MCP configuration afterward.
                        </p>
                      </div>
                    )}

                    {source.status === "scanned" && total === 0 && (
                      <p className="text-sm text-muted-foreground">Nothing found — no SKILL.md, agents/*.toml, or MCP pointer manifest.</p>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
