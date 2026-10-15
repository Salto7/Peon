import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Pencil } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { StatusBadge, ToneBadge, humanize, statusTone } from "@/components/status";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { keys, useFindings, useLiveProject, useOps } from "./live";
import type { Finding, Ops, ProjectContext } from "./types";
import { projectUrls } from "./urls";

const SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"];

export default function ResultsBoard({ ctx, ops: initial }: { ctx: ProjectContext; ops: Ops }) {
  useLiveProject(ctx.pk);
  const { data: ops } = useOps(ctx.pk, initial);
  const objectives = ops?.objectives ?? [];

  return (
    <div className="space-y-8">
      <section aria-labelledby="objectives-heading" className="space-y-3">
        <div className="flex items-baseline justify-between">
          <h2 id="objectives-heading" className="text-base font-semibold">
            Objectives
          </h2>
          <span className="text-xs text-muted-foreground">{objectives.length} total</span>
        </div>
        {objectives.length ? (
          <ol className="divide-y overflow-hidden rounded-xl border bg-card shadow-sm">
            {objectives.map((o) => (
              <li key={o.id} className="flex items-start gap-3 px-4 py-3">
                <span className="mt-0.5 w-12 shrink-0 font-mono text-xs text-muted-foreground">OBJ-{o.seq}</span>
                <div className="min-w-0 flex-1 space-y-1">
                  <p className="text-sm font-medium">{o.title}</p>
                  <p className="text-xs text-muted-foreground">
                    {o.role_id || "unassigned"} · {humanize(o.phase)}
                  </p>
                </div>
                <StatusBadge value={o.status} />
              </li>
            ))}
          </ol>
        ) : (
          <p className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">No objectives yet.</p>
        )}
      </section>
      <Findings ctx={ctx} />
    </div>
  );
}

function Findings({ ctx }: { ctx: ProjectContext }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState("all");
  const [severity, setSeverity] = useState("all");
  const [open, setOpen] = useState<Finding | null>(null);
  const { data, isLoading } = useFindings(ctx.pk, status, severity);
  const counts = data?.counts;
  const labels = Object.fromEntries(ctx.findingStatuses);

  const triage = useMutation({
    mutationFn: ({ id, status, severity }: { id: string; status?: string; severity?: string }) => {
      const fd = new FormData();
      if (status) fd.append("status", status);
      if (severity) fd.append("severity", severity);
      return api<{ seq: number; status: string; severity: string }>(projectUrls(ctx.pk).triage(id), {
        form: fd,
      });
    },
    onSuccess: (d, v) => {
      toast.success(
        v.severity
          ? `FIND-${d.seq} severity → ${humanize(d.severity)}`
          : `FIND-${d.seq} → ${labels[d.status] ?? humanize(d.status)}`,
      );
      setOpen((cur) => (cur && cur.id === v.id ? { ...cur, status: d.status, severity: d.severity } : cur));
      qc.invalidateQueries({ queryKey: ["findings", ctx.pk] });
      qc.invalidateQueries({ queryKey: keys.ops(ctx.pk) });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const findings = [...(data?.findings ?? [])].sort(
    (a, b) => SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity) || a.seq - b.seq,
  );

  return (
    <section aria-labelledby="findings-heading" className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="findings-heading" className="text-base font-semibold">
            Findings
          </h2>
          {counts && (
            <p className="text-xs text-muted-foreground">
              {counts.open} open · {counts.confirmed} confirmed · {counts.all} total
            </p>
          )}
        </div>
        <div className="flex gap-2">
          <Select value={status} onValueChange={setStatus}>
            <SelectTrigger size="sm" className="w-40" aria-label="Filter by status">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              {ctx.findingStatuses.map(([v, l]) => (
                <SelectItem key={v} value={v}>
                  {l}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={severity} onValueChange={setSeverity}>
            <SelectTrigger size="sm" className="w-36" aria-label="Filter by severity">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All severities</SelectItem>
              {ctx.findingSeverities.map(([v, l]) => (
                <SelectItem key={v} value={v}>
                  {l}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {isLoading ? (
        <div className="grid gap-3 md:grid-cols-2">
          <Skeleton className="h-32" />
          <Skeleton className="h-32" />
        </div>
      ) : findings.length ? (
        <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-3">
          {findings.map((f) => (
            <article key={f.id} className="flex flex-col gap-2 rounded-xl border bg-card p-4 shadow-sm">
              <div className="flex items-center gap-2">
                <span className="font-mono text-xs text-muted-foreground">FIND-{f.seq}</span>
                <SeverityMenu
                  value={f.severity}
                  original={f.severity_original}
                  options={ctx.findingSeverities}
                  onChange={(severity) => triage.mutate({ id: f.id, severity })}
                />
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <button
                      type="button"
                      className="ml-auto cursor-pointer rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      aria-label={`Status: ${labels[f.status] ?? f.status}. Change status`}
                    >
                      <span className="inline-flex items-center gap-0.5">
                        <StatusBadge value={f.status} label={labels[f.status]} />
                        <ChevronDown className="size-3.5 text-muted-foreground" aria-hidden />
                      </span>
                    </button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuRadioGroup
                      value={f.status}
                      onValueChange={(next) => next !== f.status && triage.mutate({ id: f.id, status: next })}
                    >
                      {ctx.findingStatuses.map(([v, l]) => (
                        <DropdownMenuRadioItem key={v} value={v}>
                          {l}
                        </DropdownMenuRadioItem>
                      ))}
                    </DropdownMenuRadioGroup>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
              <button type="button" className="cursor-pointer text-left" onClick={() => setOpen(f)}>
                <h3 className="text-sm font-medium hover:underline">{f.title}</h3>
              </button>
              {f.host && (
                <p className="font-mono text-xs text-muted-foreground">
                  {f.host}
                  {f.port ? `:${f.port}` : ""}
                </p>
              )}
              {f.evidence && <p className="line-clamp-2 text-xs text-muted-foreground">{f.evidence}</p>}
              <div className="mt-auto flex flex-wrap gap-1.5 pt-1">
                {f.kind && <ToneBadge>{f.kind}</ToneBadge>}
                {f.asset_type && <ToneBadge>{f.asset_type}</ToneBadge>}
                {f.cve_id && <ToneBadge tone="danger">{f.cve_id}</ToneBadge>}
              </div>
            </article>
          ))}
        </div>
      ) : (
        <p className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">
          {status === "all" && severity === "all"
            ? "No findings yet — agents record discoveries here with evidence."
            : "No findings match these filters."}
        </p>
      )}

      <Sheet open={!!open} onOpenChange={(o) => !o && setOpen(null)}>
        <SheetContent className="w-full gap-0 sm:max-w-xl">
          {open && (
            <>
              <SheetHeader className="border-b">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs text-muted-foreground">FIND-{open.seq}</span>
                  <SeverityMenu
                    value={open.severity}
                    original={open.severity_original}
                    options={ctx.findingSeverities}
                    onChange={(severity) => triage.mutate({ id: open.id, severity })}
                  />
                  <StatusBadge value={open.status} label={labels[open.status]} />
                </div>
                <SheetTitle>{open.title}</SheetTitle>
                <SheetDescription>
                  {[open.host && `${open.host}${open.port ? `:${open.port}` : ""}`, open.service, open.protocol]
                    .filter(Boolean)
                    .join(" · ") || "No host recorded"}
                </SheetDescription>
              </SheetHeader>
              <div className="flex-1 space-y-5 overflow-y-auto p-4 text-sm">
                <Field label="Description" value={open.description} />
                <Field label="Evidence" value={open.evidence} mono />
                {open.evidence_path && <Field label="Evidence file" value={open.evidence_path} mono />}
                <Field label="Remediation" value={open.remediation} />
                <dl className="grid grid-cols-2 gap-3 text-xs">
                  {[
                    ["Kind", open.kind],
                    ["Asset type", open.asset_type],
                    ["CVE", open.cve_id],
                    ["CWE", open.cwe_id],
                    ["MITRE", (open.mitre_techniques ?? []).join(", ")],
                  ]
                    .filter(([, v]) => v)
                    .map(([k, v]) => (
                      <div key={k}>
                        <dt className="text-muted-foreground">{k}</dt>
                        <dd className="mt-0.5 font-mono">{v}</dd>
                      </div>
                    ))}
                </dl>
              </div>
              <div className="flex justify-end gap-2 border-t p-4">
                <Button variant="outline" onClick={() => setOpen(null)}>
                  Close
                </Button>
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>
    </section>
  );
}

function Field({ label, value, mono }: { label: string; value?: string; mono?: boolean }) {
  if (!value) return null;
  return (
    <div className="space-y-1">
      <h4 className="text-xs font-medium text-muted-foreground">{label}</h4>
      <p className={mono ? "rounded-md bg-muted p-3 font-mono text-xs whitespace-pre-wrap" : "whitespace-pre-wrap"}>{value}</p>
    </div>
  );
}

/** Severity badge that opens a menu to re-rate the finding (operator override). */
function SeverityMenu({
  value,
  original,
  options,
  onChange,
}: {
  value: string;
  original?: string;
  options: [string, string][];
  onChange: (severity: string) => void;
}) {
  const changed = !!original && original !== value;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="cursor-pointer rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={`Severity: ${humanize(value)}. Change severity`}
          title={changed ? `Adjusted by operator (agent rated ${humanize(original)})` : "Change severity"}
        >
          <span className="inline-flex items-center gap-0.5">
            <ToneBadge tone={statusTone(value)}>
              {humanize(value)}
              {changed && <Pencil className="size-3" aria-hidden />}
            </ToneBadge>
            <ChevronDown className="size-3.5 text-muted-foreground" aria-hidden />
          </span>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuRadioGroup value={value} onValueChange={(next) => next !== value && onChange(next)}>
          {options.map(([v, l]) => (
            <DropdownMenuRadioItem key={v} value={v}>
              {l}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
