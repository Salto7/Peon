import { CircleAlert, CircleCheck, TriangleAlert } from "lucide-react";
import type { LintResult, PlanPreview } from "./types";

type Props = { lint: LintResult; plan: PlanPreview | null; checking: boolean };

/** Live verdict: what is wrong, and — when nothing blocks — the exact objectives this workflow runs as. */
export default function PlanPanel({ lint, plan, checking }: Props) {
  const ok = lint.errors.length === 0;
  return (
    <div className="h-full space-y-3 overflow-y-auto p-3">
      <div className="flex items-center justify-between">
        <span className={`inline-flex items-center gap-1 text-xs ${checking ? "text-muted-foreground" : ok ? "text-success" : "text-destructive"}`}>
          {checking ? "checking…" : ok ? <><CircleCheck className="size-3.5" /> runnable</> : <><CircleAlert className="size-3.5" /> {lint.errors.length} problem(s)</>}
        </span>
      </div>
      {lint.errors.length > 0 && (
        <ul className="space-y-1 rounded-lg border border-destructive/40 bg-destructive/5 p-2.5 text-xs text-destructive">
          {lint.errors.map((e, i) => (
            <li key={i} className="flex gap-1.5">
              <CircleAlert className="mt-0.5 size-3.5 shrink-0" /> {e}
            </li>
          ))}
        </ul>
      )}
      {lint.warnings.length > 0 && (
        <ul className="space-y-1 rounded-lg border border-warning/40 bg-warning/5 p-2.5 text-xs text-warning">
          {lint.warnings.map((e, i) => (
            <li key={i} className="flex gap-1.5">
              <TriangleAlert className="mt-0.5 size-3.5 shrink-0" /> {e}
            </li>
          ))}
        </ul>
      )}
      {plan ? (
        <ol className="space-y-1.5">
          {plan.objectives.map((o) => (
            <li key={o.seq} className="rounded-lg border p-2 text-xs">
              <div className="flex items-baseline gap-2">
                <span className="font-mono text-muted-foreground">OBJ-{o.seq}</span>
                <span className="truncate font-medium">{o.title}</span>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-1 text-[11px] text-muted-foreground">
                <span className="org-agent-tag">{o.role_id}</span>
                {o.skill_ids.map((s) => (
                  <span key={s} className="org-agent-tag org-agent-tag-skill">{s}</span>
                ))}
                {o.tools.map((t) => (
                  <span key={t} className="org-agent-tag">⚒ {t}</span>
                ))}
                <span className="ml-auto">after: {o.depends_on.length ? o.depends_on.map((d) => `OBJ-${d}`).join(", ") : "start"}</span>
              </div>
            </li>
          ))}
        </ol>
      ) : (
        !checking && ok && <p className="text-xs text-muted-foreground">Nothing to run yet.</p>
      )}
    </div>
  );
}
