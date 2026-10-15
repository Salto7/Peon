/**
 * window.PeonUI for the page scripts kept from the old UI (war-room console,
 * plan editor, Toolsmith, Dream, project create). Backed by the new toaster,
 * CSRF helper, status tones and the shared SSE connection.
 */
import { toast } from "sonner";
import { csrfToken } from "@/lib/api";
import { subscribe } from "@/lib/sse";
import { humanize, statusTone } from "@/components/status";

const TONE_CLASSES: Record<string, string> = {
  neutral: "border-border bg-muted text-muted-foreground",
  primary: "border-primary/25 bg-primary/10 text-link",
  success: "border-success/25 bg-success/10 text-success",
  warning: "border-warning/25 bg-warning/10 text-warning",
  danger: "border-destructive/25 bg-destructive/10 text-destructive",
  info: "border-info/25 bg-info/10 text-info",
};

function statusPill(status: string) {
  const pill = document.createElement("span");
  pill.className = `status-pill inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap ${TONE_CLASSES[statusTone(status)]}`;
  pill.dataset.status = status || "";
  pill.textContent = humanize(status);
  return pill;
}

function formatIsoTime(iso: string, opts: { timeOnly?: boolean; compact?: boolean; empty?: string } = {}) {
  const empty = opts.empty ?? "";
  if (!iso) return empty;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return empty;
  if (opts.timeOnly) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  if (opts.compact) {
    const p = (n: number) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }
  return d.toLocaleString();
}

/** Run ``fn`` every ``ms`` while visible, never overlapping. */
function poll(fn: () => unknown, ms: number) {
  let busy = false;
  let timer = 0;
  const tick = async () => {
    timer = 0;
    if (busy || document.hidden) return;
    busy = true;
    try {
      await fn();
    } catch {
      /* callers handle their own errors */
    } finally {
      busy = false;
      if (!document.hidden) timer = window.setTimeout(tick, ms);
    }
  };
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !timer && !busy) void tick();
  });
  void tick();
}

/** Coalesce bursts: at most one in-flight call plus one queued re-run. */
function serial(fn: () => unknown) {
  let running = false;
  let again = false;
  const run = async (): Promise<void> => {
    if (running) {
      again = true;
      return;
    }
    running = true;
    try {
      await fn();
    } catch {
      /* ignore */
    } finally {
      running = false;
      if (again) {
        again = false;
        void run();
      }
    }
  };
  return run;
}

/**
 * Drive a legacy console from /projects/<pk>/events/: ``onMessages`` when new
 * stream lines exist, ``onChange`` when jobs/objectives/findings/etc. change.
 * A slow safety poll covers a dropped stream.
 */
function live(pk: string, handlers: { onMessages: () => unknown; onChange: () => unknown }) {
  const messages = serial(handlers.onMessages);
  const change = serial(handlers.onChange);
  subscribe(`/projects/${pk}/events/`, {
    ready: () => {
      void messages();
      void change();
    },
    messages: () => void messages(),
    invalidate: () => void change(),
  });
  poll(async () => {
    await messages();
    await change();
  }, 30_000);
}

/** Fetch helpers for those scripts: resolve to ``{ok, status, data}``. */
async function postJson(url: string, body: unknown) {
  const res = await fetch(url, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-CSRFToken": csrfToken(),
      "X-Requested-With": "XMLHttpRequest",
    },
    body: JSON.stringify(body ?? {}),
  });
  const data = await res.json().catch(() => null);
  return { ok: res.ok, status: res.status, data };
}

async function getJson(url: string) {
  const res = await fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } });
  return { ok: res.ok, status: res.status, data: res.ok ? await res.json().catch(() => null) : null };
}

export function installLegacyShim() {
  (window as unknown as { PeonUI: unknown }).PeonUI = {
    csrfToken,
    toast: (msg: string, kind?: string) =>
      kind === "error" ? toast.error(msg) : kind === "ok" ? toast.success(msg) : kind === "warn" ? toast.warning(msg) : toast(msg),
    statusPill,
    formatIsoTime,
    poll,
    live,
    postJson,
    getJson,
  };
}
