import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export type Tone = "neutral" | "primary" | "success" | "warning" | "danger" | "info";

const toneClass: Record<Tone, string> = {
  neutral: "border-border bg-muted text-muted-foreground",
  primary: "border-primary/25 bg-primary/10 text-link",
  success: "border-success/25 bg-success/10 text-success",
  warning: "border-warning/25 bg-warning/10 text-warning",
  danger: "border-destructive/25 bg-destructive/10 text-destructive",
  info: "border-info/25 bg-info/10 text-info",
};

// Same map as peon/projects/templatetags/ui.py::_TONES.
const STATUS_TONES: Record<string, Tone> = {
  active: "success",
  running: "info",
  in_progress: "info",
  pending: "neutral",
  idle: "neutral",
  paused: "warning",
  blocked: "warning",
  finished: "neutral",
  completed: "success",
  fixed: "success",
  confirmed: "danger",
  open: "warning",
  finished_with_errors: "danger",
  failed: "danger",
  cancelled: "neutral",
  false_positive: "neutral",
  accepted: "neutral",
  critical: "danger",
  high: "danger",
  medium: "warning",
  low: "info",
  info: "neutral",
};

export const statusTone = (value?: string | null): Tone => STATUS_TONES[String(value ?? "").toLowerCase()] ?? "neutral";

export const humanize = (value?: string | null) =>
  String(value ?? "").replace(/[_-]+/g, " ").replace(/^\w/, (c) => c.toUpperCase());

export function ToneBadge({
  tone = "neutral",
  className,
  children,
}: {
  tone?: Tone;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        toneClass[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export function StatusBadge({ value, label, className }: { value?: string | null; label?: string; className?: string }) {
  const live = value === "running" || value === "in_progress";
  return (
    <ToneBadge tone={statusTone(value)} className={className}>
      <span className={cn("size-1.5 rounded-full bg-current", live && "animate-pulse")} aria-hidden />
      {label ?? humanize(value)}
    </ToneBadge>
  );
}

/** Small LED used on graph nodes. */
export function StatusDot({ value, className }: { value?: string | null; className?: string }) {
  const tone = statusTone(value);
  return (
    <span
      aria-hidden
      className={cn(
        "inline-block size-2 shrink-0 rounded-full",
        {
          neutral: "bg-muted-foreground/60",
          primary: "bg-primary",
          success: "bg-success",
          warning: "bg-warning",
          danger: "bg-destructive",
          info: "bg-info",
        }[tone],
        (value === "running" || value === "in_progress") && "animate-pulse",
        className,
      )}
    />
  );
}
