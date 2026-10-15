/**
 * Island mounting: a template renders ``<div data-island="name"
 * data-props="props-script-id"></div>`` (props via ``|json_script``; or
 * ``data-props-<key>="script-id"`` per key) and the
 * page's entry registers a lazy loader for ``name``. Each island gets its own
 * root wrapped in the shared providers.
 */
import { createElement, StrictMode, type ComponentType } from "react";
import { createRoot } from "react-dom/client";
import { Providers } from "./providers";

// Props cross the server boundary as JSON, so each island validates its own.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Loader = () => Promise<{ default: ComponentType<any> }>;

export function readJsonScript<T>(id: string | undefined | null, fallback: T): T {
  if (!id) return fallback;
  const el = document.getElementById(id);
  if (!el?.textContent) return fallback;
  try {
    return JSON.parse(el.textContent) as T;
  } catch {
    return fallback;
  }
}

export function mountIslands(registry: Record<string, Loader>) {
  document.querySelectorAll<HTMLElement>("[data-island]").forEach(async (host) => {
    const name = host.dataset.island ?? "";
    const load = registry[name];
    if (!load || host.dataset.islandMounted) return;
    host.dataset.islandMounted = "1";
    // data-props="id" supplies the whole object; data-props-<key>="id" adds
    // one key, so several islands can share a large json_script payload.
    const props = readJsonScript<Record<string, unknown>>(host.dataset.props, {});
    for (const [attr, id] of Object.entries(host.dataset)) {
      const m = attr.match(/^props([A-Z]\w*)$/);
      if (m?.[1] && id) props[m[1][0]!.toLowerCase() + m[1].slice(1)] = readJsonScript(id, null);
    }
    const { default: Component } = await load();
    createRoot(host).render(
      createElement(StrictMode, null, createElement(Providers, null, createElement(Component, props))),
    );
  });
}
