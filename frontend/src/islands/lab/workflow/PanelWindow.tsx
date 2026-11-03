import { PanelBottomClose, PanelBottomOpen, PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen } from "lucide-react";
import { type PointerEvent as ReactPointerEvent, type ReactNode, useEffect, useRef, useState } from "react";

type Edge = "left" | "right" | "top";

type Props = {
  id: string;
  title: string;
  /** Which side this panel sits on — picks the collapse icon and resize edge. */
  dock: "left" | "center" | "right" | "bottom";
  children: ReactNode;
  headerExtra?: ReactNode;
  /** Default pixel size along the resize axis (width for left/right, height for bottom). */
  defaultSize: number;
  minSize?: number;
  maxSize?: number;
  className?: string;
  collapsedRailPx?: number;
  storageKey?: string;
};

type Stored = { size: number; collapsed: boolean };

function load(key: string, fallback: Stored): Stored {
  try {
    const raw = JSON.parse(localStorage.getItem(key) || "");
    if (!raw || typeof raw !== "object") return fallback;
    return {
      size: typeof raw.size === "number" ? raw.size : fallback.size,
      collapsed: !!raw.collapsed,
    };
  } catch {
    return fallback;
  }
}

function save(key: string, value: Stored) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* private mode */
  }
}

const ICONS = {
  left: { open: PanelLeftClose, closed: PanelLeftOpen },
  right: { open: PanelRightClose, closed: PanelRightOpen },
  center: { open: PanelBottomClose, closed: PanelBottomOpen },
  bottom: { open: PanelBottomClose, closed: PanelBottomOpen },
} as const;

/**
 * War-room-style window: collapse to a rail/header, drag the free edge to resize.
 * Bottom panels resize from their top edge (the split with the canvas row).
 * Sizes persist in localStorage; flex siblings grow into freed space.
 */
export default function PanelWindow({
  id,
  title,
  dock,
  children,
  headerExtra,
  defaultSize,
  minSize = 160,
  maxSize = 900,
  className = "",
  collapsedRailPx = 44,
  storageKey,
}: Props) {
  const key = storageKey ?? `peon-wf-panel-${id}`;
  const [state, setState] = useState<Stored>(() => load(key, { size: defaultSize, collapsed: false }));
  const drag = useRef<{ edge: Edge; start: number; size: number } | null>(null);
  // Free edge facing the neighboring panel / canvas split.
  const edge: Edge | null =
    dock === "left" ? "right" : dock === "right" ? "left" : dock === "bottom" ? "top" : null;
  const IconOpen = ICONS[dock].open;
  const IconClosed = ICONS[dock].closed;

  useEffect(() => {
    save(key, state);
    window.dispatchEvent(new Event("resize"));
  }, [key, state]);

  useEffect(() => {
    const onMove = (ev: PointerEvent) => {
      const d = drag.current;
      if (!d) return;
      const cur = d.edge === "top" ? ev.clientY : ev.clientX;
      const delta = cur - d.start;
      // right dock: drag leftward grows; top edge: drag upward grows
      const signed = dock === "right" ? -delta : dock === "bottom" ? -delta : delta;
      const next = Math.min(maxSize, Math.max(minSize, Math.round(d.size + signed)));
      setState((s) => (s.size === next ? s : { ...s, size: next }));
    };
    const onUp = () => {
      drag.current = null;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
  }, [dock, maxSize, minSize]);

  const setCollapsed = (collapsed: boolean) => setState((s) => ({ ...s, collapsed }));

  const onResizeDown = (ev: ReactPointerEvent<HTMLSpanElement>) => {
    if (state.collapsed || !edge) return;
    ev.preventDefault();
    ev.stopPropagation();
    drag.current = {
      edge,
      start: edge === "top" ? ev.clientY : ev.clientX,
      size: state.size,
    };
    document.body.style.cursor = edge === "top" ? "ns-resize" : "ew-resize";
    document.body.style.userSelect = "none";
    (ev.currentTarget as HTMLElement).setPointerCapture(ev.pointerId);
  };

  const sizeStyle =
    dock === "bottom"
      ? { height: state.collapsed ? undefined : state.size, flex: state.collapsed ? "0 0 auto" : "1 1 0" }
      : dock === "center"
        ? undefined
        : {
            width: state.collapsed ? collapsedRailPx : state.size,
            flex: "0 0 auto",
          };

  return (
    <section
      data-wf-panel={id}
      data-collapsed={state.collapsed ? "true" : undefined}
      className={`wf-panel wf-panel-${dock} relative flex min-h-0 min-w-0 flex-col overflow-hidden rounded-xl border bg-card shadow-sm ${
        dock === "center" ? (state.collapsed ? "flex-none self-start" : "min-h-[22rem] flex-1") : ""
      } ${className}`}
      style={sizeStyle}
    >
      <header className="wf-panel-header flex shrink-0 items-center gap-2 border-b px-2.5 py-1.5">
        <h2 className="text-sm font-semibold">{title}</h2>
        {!state.collapsed && headerExtra}
        <button
          type="button"
          className="panel-hide ml-auto"
          aria-expanded={!state.collapsed}
          title={state.collapsed ? `Expand ${title}` : `Collapse ${title}`}
          aria-label={state.collapsed ? `Expand ${title}` : `Collapse ${title}`}
          onClick={() => setCollapsed(!state.collapsed)}
        >
          {state.collapsed ? <IconClosed className="size-4" /> : <IconOpen className="size-4" />}
        </button>
      </header>
      {!state.collapsed && (
        <div className="wf-panel-body flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">{children}</div>
      )}
      {!state.collapsed && edge && (
        <span
          role="separator"
          aria-orientation={edge === "top" ? "horizontal" : "vertical"}
          aria-label={`Resize ${title}`}
          className={`wf-panel-resizer wf-panel-resizer-${edge}`}
          onPointerDown={onResizeDown}
          title="Drag to resize"
        />
      )}
    </section>
  );
}
