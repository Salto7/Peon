/** Theme lives on <html data-theme>; base templates set it before first paint. */
export type Theme = "dark" | "light";

const KEY = "peon-theme";

export function getTheme(): Theme {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

export function setTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    /* storage may be blocked; the attribute still applies for this page */
  }
  // Legacy listeners (xterm in terminal.js) re-theme on this event.
  window.dispatchEvent(new CustomEvent("peon:themechange", { detail: { theme } }));
}
