import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FolderOpen, Loader2, Upload } from "lucide-react";
import { useId, useRef, useState, type DragEvent } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import { requestConfirm } from "@/lib/behaviors";
import {
  canPickDirectory,
  pairsFromDrop,
  pairsFromFileList,
  pickDirectory,
  uploadBody,
  type UploadPair,
} from "@/lib/folder-upload";
import { cn } from "@/lib/utils";

type Source = { value: "npx" | "git" | "disk"; label: string; placeholder?: string };

/** MCP installs still return the plain ``{ids}`` shape; skill installs also
 * report per-pack outcomes so a name collision can be put to the operator
 * instead of either blocking the whole batch or silently overwriting. */
type InstallResult = {
  ids?: string[];
  installed?: string[];
  skipped?: string[];
  conflicts?: string[];
  errors?: { path: string; error: string }[];
};

/** What to replay with `on_conflict` once the operator decides. */
type LastAttempt =
  | { via: "source"; kind: Source["value"]; value: string; revision: string }
  | { via: "upload"; pairs: UploadPair[] };

type Props = {
  /** "skill" or "mcp": MCP uploads drop hidden paths. */
  kind: "skill" | "mcp";
  installUrl: string;
  uploadUrl: string;
  sources: Source[];
  submitLabel: string;
  help?: string;
};

/**
 * Install a skill pack or MCP server from a marketplace/git source, or by
 * dropping/picking a folder (or .zip). Reloads the page on success so the
 * server-rendered catalog shows the new entry.
 */
export default function InstallForm({ kind, installUrl, uploadUrl, sources, submitLabel, help }: Props) {
  const ids = useId();
  const qc = useQueryClient();
  const [source, setSource] = useState<Source["value"]>(sources[0]?.value ?? "git");
  const [value, setValue] = useState("");
  const [revision, setRevision] = useState("");
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const zipInput = useRef<HTMLInputElement>(null);
  const lastAttempt = useRef<LastAttempt | null>(null);
  const rules = { skipHidden: kind === "mcp" };
  const current = sources.find((s) => s.value === source);

  const reload = () => {
    qc.invalidateQueries({ queryKey: ["catalog-proposals"] });
    window.setTimeout(() => window.location.reload(), 600);
  };

  const retry = (attempt: LastAttempt) => {
    if (attempt.via === "source") install.mutate({ ...attempt, onConflict: "overwrite" });
    else upload.mutate({ pairs: attempt.pairs, onConflict: "overwrite" });
  };

  const onDone = (data: InstallResult) => {
    const installedNow = data.installed ?? data.ids ?? [];
    const skipped = data.skipped ?? [];
    const conflicts = data.conflicts ?? [];
    const errors = data.errors ?? [];
    if (installedNow.length) toast.success(`Installed ${installedNow.join(", ")}`);
    if (skipped.length) toast.info(`Already installed, skipped: ${skipped.join(", ")}`);
    for (const e of errors) toast.error(`${e.path}: ${e.error}`);
    if (conflicts.length && lastAttempt.current) {
      const attempt = lastAttempt.current;
      requestConfirm({
        title: conflicts.length === 1 ? "Skill already exists" : "Skills already exist",
        message:
          conflicts.length === 1
            ? `"${conflicts[0]}" already exists. Overwrite it with this install?`
            : `${conflicts.length} skills already exist: ${conflicts.join(", ")}. Overwrite them with this install?`,
        confirmLabel: "Overwrite",
        destructive: true,
        onConfirm: () => retry(attempt),
      });
      // Don't schedule the page reload here — it would fire mid-dialog and
      // yank the "overwrite?" confirm away before the operator can answer.
      // The retried install's own onDone reloads once conflicts clear.
      if (installedNow.length) qc.invalidateQueries({ queryKey: ["catalog-proposals"] });
      return;
    }
    if (!installedNow.length && !skipped.length) return;
    reload();
  };
  const onError = (err: Error) => toast.error(err.message);

  const install = useMutation({
    mutationFn: (args: { kind: Source["value"]; value: string; revision: string; onConflict?: "overwrite" }) =>
      api<InstallResult>(installUrl, {
        json: {
          kind: args.kind,
          source: args.value.trim(),
          revision: args.revision.trim(),
          ...(args.onConflict ? { on_conflict: args.onConflict } : {}),
        },
      }),
    onSuccess: onDone,
    onError,
  });
  const upload = useMutation({
    mutationFn: (args: { pairs: UploadPair[]; onConflict?: "overwrite" }) =>
      api<InstallResult>(uploadUrl, {
        form: uploadBody(args.pairs, args.onConflict ? { on_conflict: args.onConflict } : undefined),
      }),
    onSuccess: onDone,
    onError,
  });
  const busy = install.isPending || upload.isPending;

  const send = (pairs: UploadPair[] | null) => {
    if (pairs === null) return;
    if (!pairs.length) {
      toast.error("Nothing to install — the folder only had ignored or hidden paths.");
      return;
    }
    toast.info(`Uploading ${pairs.length} file${pairs.length === 1 ? "" : "s"}…`);
    lastAttempt.current = { via: "upload", pairs };
    upload.mutate({ pairs });
  };

  const chooseFolder = async () => {
    if (canPickDirectory()) {
      try {
        send(await pickDirectory(rules));
      } catch (err) {
        onError(err as Error);
      }
    } else {
      fileInput.current?.click();
    }
  };

  const onDrop = async (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    send(await pairsFromDrop(e.dataTransfer, rules));
  };

  return (
    <div className="min-w-0 space-y-4">
      <form
        className="min-w-0 space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (source === "disk") return;
          lastAttempt.current = { via: "source", kind: source, value, revision };
          install.mutate({ kind: source, value, revision });
        }}
      >
        <div className="space-y-1.5">
          <Label htmlFor={`${ids}-source`}>Source</Label>
          <Select value={source} onValueChange={(v) => setSource(v as Source["value"])}>
            <SelectTrigger id={`${ids}-source`} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {sources.map((s) => (
                <SelectItem key={s.value} value={s.value}>
                  {s.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {source === "disk" ? (
          <div className="space-y-1.5">
            <span className="text-sm leading-none font-medium">Folder</span>
            <div
              role="group"
              aria-label="Drop a folder or zip"
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={onDrop}
              className={cn(
                "flex min-w-0 flex-col gap-3 rounded-lg border border-dashed px-4 py-5 text-sm text-muted-foreground transition-colors sm:flex-row sm:items-center",
                dragging && "border-primary bg-primary/5 text-foreground",
              )}
            >
              <div className="flex min-w-0 flex-1 items-start gap-3">
                <Upload className="mt-0.5 size-4 shrink-0" aria-hidden />
                <span className="min-w-0 break-words">Drop a folder or .zip here</span>
              </div>
              <div className="flex shrink-0 flex-wrap items-center gap-2">
                <Button type="button" variant="outline" size="sm" disabled={busy} onClick={chooseFolder}>
                  <FolderOpen /> Choose folder
                </Button>
                {kind === "skill" && (
                  <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => zipInput.current?.click()}>
                    Upload .zip
                  </Button>
                )}
                {busy && <Loader2 className="size-4 animate-spin" aria-label="Uploading" />}
              </div>
            </div>
            <input
              ref={fileInput}
              type="file"
              className="sr-only"
              tabIndex={-1}
              aria-hidden
              multiple
              {...({ webkitdirectory: "" } as Record<string, string>)}
              onChange={(e) => {
                if (e.target.files) send(pairsFromFileList(e.target.files, rules));
                e.target.value = "";
              }}
            />
            <input
              ref={zipInput}
              type="file"
              accept=".zip"
              className="sr-only"
              tabIndex={-1}
              aria-hidden
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) send([{ file, path: file.name }]);
                e.target.value = "";
              }}
            />
          </div>
        ) : (
          <>
            <div className="grid min-w-0 gap-3 sm:grid-cols-[minmax(0,1fr)_9rem]">
              <div className="min-w-0 space-y-1.5">
                <Label htmlFor={`${ids}-value`}>{source === "git" ? "Repository" : "Package"}</Label>
                <Input
                  id={`${ids}-value`}
                  required
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                  placeholder={current?.placeholder}
                  className="min-w-0"
                />
              </div>
              {source === "git" ? (
                <div className="min-w-0 space-y-1.5">
                  <Label htmlFor={`${ids}-rev`}>Revision</Label>
                  <Input
                    id={`${ids}-rev`}
                    value={revision}
                    onChange={(e) => setRevision(e.target.value)}
                    placeholder="main"
                    className="min-w-0"
                  />
                </div>
              ) : (
                <div className="hidden sm:block" aria-hidden />
              )}
            </div>
            <div className="flex justify-end">
              <Button type="submit" disabled={busy || !value.trim()} className="w-full sm:w-auto">
                {install.isPending && <Loader2 className="animate-spin" />}
                {submitLabel}
              </Button>
            </div>
          </>
        )}
      </form>
      {help ? <p className="text-xs break-words text-muted-foreground">{help}</p> : null}
    </div>
  );
}
