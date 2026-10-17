/**
 * Progressive enhancements for server-rendered pages, bound once by delegation.
 *
 *  - ``data-confirm="message"`` on a <form> or submit button → AlertDialog first
 *    (``data-confirm-label``, ``data-confirm-title``; destructive by default).
 *  - ``<form data-bulk id=X>`` with ``[data-bulk-row][form=X]`` checkboxes,
 *    ``[data-bulk-all]``, ``[data-bulk-count]`` and ``[data-bulk-btn]``.
 *  - ``[data-collapse-toggle=ID]`` button toggles ``data-collapsed`` on ``#ID``
 *    (state remembered in localStorage).
 *  - ``[data-maximize-toggle=ID]`` button toggles ``data-maximized`` on ``#ID``
 *    (full-viewport panel; Esc exits).
 *  - ``<details data-menu>`` dropdowns close on outside click / Escape.
 */
export const CONFIRM_EVENT = "peon:confirm";

export type ConfirmRequest = {
  message: string;
  title?: string;
  confirmLabel?: string;
  destructive?: boolean;
  onConfirm: () => void;
};

export function requestConfirm(req: ConfirmRequest) {
  window.dispatchEvent(new CustomEvent(CONFIRM_EVENT, { detail: req }));
}

const confirmed = new WeakSet<HTMLFormElement>();

function bindConfirm() {
  document.addEventListener("submit", (e) => {
    const form = e.target as HTMLFormElement;
    const submitter = (e as SubmitEvent).submitter as HTMLButtonElement | null;
    const source = submitter?.dataset.confirm ? submitter : form;
    const message = source.dataset.confirm;
    if (!message) return;
    if (confirmed.has(form)) {
      confirmed.delete(form);
      return;
    }
    e.preventDefault();
    requestConfirm({
      message,
      title: source.dataset.confirmTitle,
      confirmLabel: source.dataset.confirmLabel ?? "Delete",
      destructive: source.dataset.confirmTone !== "default",
      onConfirm: () => {
        confirmed.add(form);
        form.requestSubmit(submitter ?? undefined);
      },
    });
  });
}

function syncBulk(form: HTMLFormElement) {
  const rows = [...document.querySelectorAll<HTMLInputElement>(`[data-bulk-row][form="${form.id}"]`)];
  const n = rows.filter((r) => r.checked).length;
  form.querySelectorAll<HTMLElement>("[data-bulk-count]").forEach((el) => {
    el.textContent = `${n} selected`;
  });
  form.querySelectorAll<HTMLButtonElement>("[data-bulk-btn]").forEach((b) => {
    b.disabled = n === 0;
  });
  form.toggleAttribute("data-bulk-active", n > 0);
  document.querySelectorAll<HTMLInputElement>(`[data-bulk-all="${form.id}"]`).forEach((all) => {
    all.checked = n > 0 && n === rows.length;
    all.indeterminate = n > 0 && n < rows.length;
  });
}

function bindBulk() {
  document.addEventListener("change", (e) => {
    const t = e.target as HTMLInputElement;
    const allFor = t.dataset.bulkAll;
    const formId = allFor ?? (t.hasAttribute("data-bulk-row") ? t.getAttribute("form") : null);
    if (!formId) return;
    if (allFor) {
      document
        .querySelectorAll<HTMLInputElement>(`[data-bulk-row][form="${formId}"]`)
        .forEach((r) => (r.checked = t.checked));
    }
    const form = document.getElementById(formId);
    if (form instanceof HTMLFormElement) syncBulk(form);
  });
  document.querySelectorAll<HTMLFormElement>("form[data-bulk]").forEach(syncBulk);
}

/**
 * The panel is placed with ``position: fixed`` from the trigger's rect so it
 * escapes ``overflow`` containers (tables) instead of being clipped/scrolling.
 */
function placeMenu(menu: HTMLDetailsElement) {
  const trigger = menu.querySelector("summary");
  const panel = menu.querySelector<HTMLElement>("[data-menu-panel]");
  if (!trigger || !panel) return;
  const r = trigger.getBoundingClientRect();
  panel.style.position = "fixed";
  panel.style.visibility = "hidden";
  panel.style.top = "0px";
  panel.style.left = "0px";
  const { width, height } = panel.getBoundingClientRect();
  const alignLeft = menu.dataset.menuAlign === "left";
  const left = alignLeft ? r.left : r.right - width;
  const below = r.bottom + 4;
  const top = below + height > window.innerHeight - 8 ? r.top - height - 4 : below;
  panel.style.left = `${Math.max(8, Math.min(left, window.innerWidth - width - 8))}px`;
  panel.style.top = `${Math.max(8, top)}px`;
  panel.style.visibility = "";
}

function bindMenus() {
  const openMenus = () => document.querySelectorAll<HTMLDetailsElement>("details[data-menu][open]");
  const closeAll = (except?: Element | null) =>
    openMenus().forEach((d) => {
      if (d !== except) d.open = false;
    });
  document.addEventListener(
    "toggle",
    (e) => {
      const menu = e.target as HTMLElement;
      if (menu instanceof HTMLDetailsElement && menu.matches("[data-menu]") && menu.open) {
        placeMenu(menu);
      }
    },
    true,
  );
  document.addEventListener("click", (e) => {
    closeAll((e.target as HTMLElement).closest("details[data-menu]"));
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeAll();
  });
  // Keep fixed panels attached to their trigger while the page moves.
  let frame = 0;
  const reposition = () => {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      openMenus().forEach(placeMenu);
    });
  };
  window.addEventListener("resize", reposition);
  document.addEventListener("scroll", reposition, true);
}

function collapseKey(id: string) {
  return `peon:collapse:${id}`;
}

function applyCollapse(id: string, collapsed: boolean) {
  const target = document.getElementById(id);
  if (!target) return;
  target.toggleAttribute("data-collapsed", collapsed);
  document
    .querySelectorAll<HTMLElement>(`[data-collapse-toggle="${id}"]`)
    .forEach((btn) => {
      btn.setAttribute("aria-expanded", String(!collapsed));
      btn.title = collapsed ? "Expand" : "Collapse";
    });
}

function bindCollapse() {
  document.querySelectorAll<HTMLElement>("[data-collapse-toggle]").forEach((btn) => {
    const id = btn.dataset.collapseToggle ?? "";
    let stored = false;
    try {
      stored = localStorage.getItem(collapseKey(id)) === "1";
    } catch {
      /* storage unavailable */
    }
    applyCollapse(id, stored);
  });
  document.addEventListener("click", (e) => {
    const btn = (e.target as Element).closest<HTMLElement>("[data-collapse-toggle]");
    if (!btn) return;
    const id = btn.dataset.collapseToggle ?? "";
    const target = document.getElementById(id);
    if (!target) return;
    const collapsed = !target.hasAttribute("data-collapsed");
    applyCollapse(id, collapsed);
    try {
      localStorage.setItem(collapseKey(id), collapsed ? "1" : "0");
    } catch {
      /* storage unavailable */
    }
  });
}

function setMaximized(target: HTMLElement, on: boolean) {
  target.toggleAttribute("data-maximized", on);
  document.body.style.overflow = on ? "hidden" : "";
  document
    .querySelectorAll<HTMLElement>(`[data-maximize-toggle="${target.id}"]`)
    .forEach((btn) => {
      btn.setAttribute("aria-pressed", String(on));
      const label = on ? "Exit full screen (Esc)" : "Maximise graph";
      btn.title = label;
      btn.setAttribute("aria-label", label);
    });
  // Let the layout settle, then ask graph widgets to re-centre / re-fit.
  requestAnimationFrame(() => {
    window.dispatchEvent(new Event("resize"));
    target.querySelector<HTMLElement>('[data-ag-zoom="center"]')?.click();
  });
}

function bindMaximize() {
  document.addEventListener("click", (e) => {
    const btn = (e.target as Element).closest<HTMLElement>("[data-maximize-toggle]");
    if (!btn) return;
    const target = document.getElementById(btn.dataset.maximizeToggle ?? "");
    if (target) setMaximized(target, !target.hasAttribute("data-maximized"));
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const open = document.querySelector<HTMLElement>("[data-maximized]");
    if (open?.id) setMaximized(open, false);
  });
}

export function bindBehaviors() {
  bindConfirm();
  bindBulk();
  bindCollapse();
  bindMaximize();
  bindMenus();
}
