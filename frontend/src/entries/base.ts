/** Loaded on every page (peon/templates/base.html). */
import "@/styles/app.css";
import { mountIslands } from "@/islands/mount";
import { bindBehaviors } from "@/lib/behaviors";
import { installLegacyShim } from "@/lib/legacy-shim";
import { getTheme, setTheme } from "@/lib/theme";

const SIDEBAR_KEY = "peon-sidebar-collapsed";

function bindShell() {
  const root = document.documentElement;

  document.addEventListener("click", (e) => {
    const target = e.target as HTMLElement;
    if (target.closest("[data-sidebar-collapse]")) {
      const collapsed = root.toggleAttribute("data-sidebar-collapsed");
      try {
        localStorage.setItem(SIDEBAR_KEY, collapsed ? "1" : "0");
      } catch {
        /* non-essential */
      }
    } else if (target.closest("[data-sidebar-open]")) {
      root.toggleAttribute("data-sidebar-open", true);
    } else if (target.closest("[data-sidebar-dismiss]")) {
      root.removeAttribute("data-sidebar-open");
    } else if (target.closest("[data-theme-toggle]")) {
      setTheme(getTheme() === "dark" ? "light" : "dark");
    } else if (target.closest("[data-theme-set]")) {
      setTheme(target.closest<HTMLElement>("[data-theme-set]")!.dataset.themeSet === "light" ? "light" : "dark");
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") root.removeAttribute("data-sidebar-open");
  });
}

bindShell();
bindBehaviors();

installLegacyShim();
mountIslands({
  "command-palette": () => import("@/islands/global/CommandPalette"),
  toaster: () => import("@/islands/global/Toaster"),
  "hitl-prompt": () => import("@/islands/global/HitlPrompt"),
  confirm: () => import("@/islands/global/ConfirmDialog"),
  "install-form": () => import("@/islands/catalog/InstallForm"),
  "catalog-editor": () => import("@/islands/catalog/CatalogEditor"),
  "project-header": () => import("@/islands/war-room/ProjectHeader"),
  "results-board": () => import("@/islands/war-room/ResultsBoard"),
  "attack-surface": () => import("@/islands/war-room/AttackSurface"),
  "memory-panel": () => import("@/islands/war-room/MemoryPanel"),
});
