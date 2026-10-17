import { ChevronRight, Ellipsis, ExternalLink, SkipForward } from "lucide-react";
import { StatusBadge, ToneBadge } from "@/components/status";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useProjectControl } from "./actions";
import { useLiveProject, useOps } from "./live";
import type { Ops, ProjectContext } from "./types";

export default function ProjectHeader({
  ctx,
  ops: initial,
  actions,
}: {
  ctx: ProjectContext;
  ops: Ops;
  actions?: string | null;
}) {
  const live = useLiveProject(ctx.pk);
  const { data: ops } = useOps(ctx.pk, initial);
  const control = useProjectControl(ctx.pk);
  const status = ops?.project.status ?? "active";
  const p = ops?.project.progress;
  const busy = control.isPending;
  const inactive = ["cancelled", "finished", "finished_with_errors"].includes(status);

  return (
    <header className="space-y-4">
      <nav aria-label="Breadcrumb" className="flex items-center gap-1 text-xs text-muted-foreground">
        <a className="hover:text-foreground" href="/projects/">
          Projects
        </a>
        <ChevronRight className="size-3" aria-hidden />
        <span className="truncate">{ctx.title}</span>
      </nav>
      <div className="space-y-1.5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <h1 className="truncate text-xl font-semibold tracking-tight">{ctx.title}</h1>
            <StatusBadge value={status} />
            <ToneBadge>{ctx.kind === "durable" ? "Durable" : "Timed"}</ToneBadge>
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
                  <span className={live ? "size-1.5 rounded-full bg-success" : "size-1.5 rounded-full bg-muted-foreground/50"} />
                  {live ? "Live" : "Connecting…"}
                </span>
              </TooltipTrigger>
              <TooltipContent>{live ? "Receiving live updates" : "Reconnecting to the live stream"}</TooltipContent>
            </Tooltip>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
          {actions ? (
            <div
              className="flex flex-wrap items-center gap-2"
              dangerouslySetInnerHTML={{ __html: actions }}
            />
          ) : null}
          {!inactive && (
            <Button
              variant="outline"
              disabled={busy || !ops?.next_objective_ready}
              title={ops?.next_objective_ready ? undefined : "No ready objective"}
              onClick={() => control.mutate("run_next")}
            >
              <SkipForward /> Run next
            </Button>
          )}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" aria-label="More project actions">
                <Ellipsis />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-48">
              {ctx.planUrl && (
                <DropdownMenuItem asChild>
                  <a href={ctx.planUrl}>
                    <ExternalLink /> Plan graph
                  </a>
                </DropdownMenuItem>
              )}
              <DropdownMenuItem asChild>
                <a href="#reports">
                  <ExternalLink /> Reports
                </a>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          </div>
        </div>
        {ctx.summary && <p className="max-w-3xl text-sm text-muted-foreground">{ctx.summary}</p>}
      </div>

      <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Objectives" value={p ? `${p.objectives_done}/${p.objectives_total}` : "—"}>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary transition-[width] duration-500"
              style={{ width: `${Math.round((p?.ratio ?? 0) * 100)}%` }}
            />
          </div>
        </Stat>
        <Stat label="Agents finished" value={p ? `${p.jobs_done}/${p.jobs_total}` : "—"} />
        <Stat label="Phase" value={ops?.project.phase ? ops.project.phase : "—"} />
        <Stat label="Sandbox" value={ops?.project.sandbox?.mode ?? ctx.runtime} mono={ops?.project.sandbox?.name} />
      </dl>
    </header>
  );
}

function Stat({ label, value, mono, children }: { label: string; value: string; mono?: string; children?: React.ReactNode }) {
  return (
    <div className="min-w-0 rounded-xl border bg-card px-4 py-3 shadow-sm">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 truncate text-lg font-semibold tabular-nums capitalize">{value}</dd>
      {mono && (
        <dd className="truncate font-mono text-[11px] text-muted-foreground" title={mono}>
          {mono}
        </dd>
      )}
      {children}
    </div>
  );
}
