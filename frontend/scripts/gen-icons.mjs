// Extract the lucide icons used by Django templates into a small JSON map
// consumed by the {% icon %} tag (peon/projects/templatetags/ui.py).
// Add a name here and run `npm run icons` to make it available.
import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const ICONS = [
  "activity", "bot", "brain", "chevron-down", "chevron-right", "circle-alert", "circle-check",
  "crosshair", "ellipsis", "external-link", "file-text", "flask-conical", "folder-kanban",
  "folder-open", "hammer", "house", "inbox", "layout-dashboard", "library", "list-checks",
  "menu", "message-square", "moon", "network", "panel-left", "pause", "play", "plus",
  "refresh-cw", "scroll-text", "search", "server", "settings", "shield", "sparkles",
  "square-terminal", "sun", "target", "trash-2", "triangle-alert", "upload", "wrench", "x",
];

const require = createRequire(import.meta.url);
const dir = path.join(path.dirname(require.resolve("lucide-static/package.json")), "icons");
const out = {};
for (const name of ICONS) {
  const svg = readFileSync(path.join(dir, `${name}.svg`), "utf8");
  const inner = svg.slice(svg.indexOf(">", svg.indexOf("<svg")) + 1, svg.lastIndexOf("</svg>"));
  out[name] = inner.replace(/\s*\n\s*/g, "").trim();
}
const target = path.resolve(import.meta.dirname, "../../peon/projects/templatetags/lucide_icons.json");
writeFileSync(target, JSON.stringify(out, null, 0) + "\n");
console.log(`wrote ${Object.keys(out).length} icons -> ${path.relative(process.cwd(), target)}`);
