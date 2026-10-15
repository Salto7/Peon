/**
 * Collect ``{file, path}`` pairs from a folder picker, a directory handle or a
 * drag-and-drop, applying the catalog's ignore rules. One implementation for
 * every install surface (catalog skills and MCP servers).
 */
export type UploadPair = { file: File; path: string };

const SKIP_DIRS = new Set([
  ".venv", "venv", "node_modules", ".git", "__pycache__", ".tox",
  ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
]);

export type UploadRules = {
  /** MCP installs drop hidden paths (.env, .venv…) except a top-level .mcp.json. */
  skipHidden: boolean;
};

function skipPart(name: string, rules: UploadRules, isLeaf: boolean) {
  if (!name || name === "." || name === "..") return true;
  if (SKIP_DIRS.has(name)) return true;
  if (rules.skipHidden && name.startsWith(".")) return !(isLeaf && name === ".mcp.json");
  return false;
}

function normalise(path: string, rules: UploadRules): string | null {
  const parts = path.replace(/\\/g, "/").split("/").filter(Boolean);
  if (parts.some((p, i) => skipPart(p, rules, i === parts.length - 1))) return null;
  // The server never depends on a hidden name.
  if (parts[parts.length - 1] === ".mcp.json") parts[parts.length - 1] = "mcp.json";
  return parts.join("/");
}

function accept(out: UploadPair[], file: File, path: string, rules: UploadRules) {
  const rel = normalise(path, rules);
  if (rel) out.push({ file, path: rel });
}

export function pairsFromFileList(list: FileList | File[], rules: UploadRules): UploadPair[] {
  const out: UploadPair[] = [];
  for (const file of Array.from(list)) accept(out, file, file.webkitRelativePath || file.name, rules);
  return out;
}

type DirHandle = {
  name: string;
  entries(): AsyncIterable<[string, { kind: "file"; getFile(): Promise<File> } | (DirHandle & { kind: "directory" })]>;
};

export async function pairsFromDirectoryHandle(root: DirHandle, rules: UploadRules) {
  const out: UploadPair[] = [];
  async function walk(dir: DirHandle, prefix: string) {
    for await (const [name, child] of dir.entries()) {
      if (skipPart(name, rules, child.kind === "file")) continue;
      const rel = `${prefix}/${name}`;
      if (child.kind === "file") accept(out, await child.getFile(), rel, rules);
      else await walk(child, rel);
    }
  }
  await walk(root, root.name);
  return out;
}

export function canPickDirectory(): boolean {
  return typeof (window as unknown as { showDirectoryPicker?: unknown }).showDirectoryPicker === "function";
}

export async function pickDirectory(rules: UploadRules): Promise<UploadPair[] | null> {
  const picker = (window as unknown as { showDirectoryPicker: () => Promise<DirHandle> }).showDirectoryPicker;
  try {
    return await pairsFromDirectoryHandle(await picker(), rules);
  } catch (err) {
    if ((err as DOMException).name === "AbortError") return null;
    throw err;
  }
}

function readEntries(reader: FileSystemDirectoryReader) {
  return new Promise<FileSystemEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));
}

function entryFile(entry: FileSystemFileEntry) {
  return new Promise<File>((resolve, reject) => entry.file(resolve, reject));
}

export async function pairsFromDrop(data: DataTransfer, rules: UploadRules): Promise<UploadPair[]> {
  const out: UploadPair[] = [];
  async function walk(entry: FileSystemEntry, prefix: string) {
    if (entry.isFile) {
      accept(out, await entryFile(entry as FileSystemFileEntry), prefix + entry.name, rules);
    } else if (entry.isDirectory) {
      if (skipPart(entry.name, rules, false)) return;
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      for (;;) {
        const batch = await readEntries(reader);
        if (!batch.length) break;
        for (const child of batch) await walk(child, `${prefix}${entry.name}/`);
      }
    }
  }
  // Grab entries synchronously: DataTransfer items are cleared after the first await.
  const entries = Array.from(data.items)
    .map((item) => item.webkitGetAsEntry?.())
    .filter((e): e is FileSystemEntry => !!e);
  if (entries.length) {
    for (const entry of entries) await walk(entry, "");
    return out;
  }
  return pairsFromFileList(data.files, rules);
}

export function uploadBody(pairs: UploadPair[]): FormData {
  const body = new FormData();
  for (const { file, path } of pairs) {
    body.append("file", file, file.name);
    body.append("path", path);
  }
  return body;
}
