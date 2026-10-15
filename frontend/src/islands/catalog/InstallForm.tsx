import { useMutation } from "@tanstack/react-query";
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
  const [source, setSource] = useState<Source["value"]>(sources[0]?.value ?? "git");
  const [value, setValue] = useState("");
  const [revision, setRevision] = useState("");
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const zipInput = useRef<HTMLInputElement>(null);
  const rules = { skipHidden: kind === "mcp" };
  const current = sources.find((s) => s.value === source);

  const onDone = (data: { ids?: string[] }) => {
    toast.success(`Installed ${(data.ids ?? []).join(", ") || submitLabel.toLowerCase()}`);
    window.setTimeout(() => window.location.reload(), 600);
  };
  const onError = (err: Error) => toast.error(err.message);

  const install = useMutation({
    mutationFn: () =>
      api<{ ids?: string[] }>(installUrl, { json: { kind: source, source: value.trim(), revision: revision.trim() } }),
    onSuccess: onDone,
    onError,
  });
  const upload = useMutation({
    mutationFn: (pairs: UploadPair[]) => api<{ ids?: string[] }>(uploadUrl, { form: uploadBody(pairs) }),
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
    upload.mutate(pairs);
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
    <div className="space-y-3">
      <form
        className="grid gap-3 sm:grid-cols-[11rem_1fr_auto]"
        onSubmit={(e) => {
          e.preventDefault();
          if (source !== "disk") install.mutate();
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
          <div className="space-y-1.5 sm:col-span-2">
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
                "flex min-h-9 flex-wrap items-center gap-3 rounded-md border border-dashed px-3 py-2 text-sm text-muted-foreground transition-colors",
                dragging && "border-primary bg-primary/5 text-foreground",
              )}
            >
              <Upload className="size-4" aria-hidden />
              <span className="flex-1">Drop a folder or .zip here</span>
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
            <div className="grid gap-3 sm:grid-cols-[1fr_10rem]">
              <div className="space-y-1.5">
                <Label htmlFor={`${ids}-value`}>{source === "git" ? "Repository" : "Package"}</Label>
                <Input
                  id={`${ids}-value`}
                  required
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                  placeholder={current?.placeholder}
                />
              </div>
              {source === "git" && (
                <div className="space-y-1.5">
                  <Label htmlFor={`${ids}-rev`}>Revision</Label>
                  <Input
                    id={`${ids}-rev`}
                    value={revision}
                    onChange={(e) => setRevision(e.target.value)}
                    placeholder="main (optional)"
                  />
                </div>
              )}
            </div>
            <div className="flex items-end">
              <Button type="submit" disabled={busy || !value.trim()} className="w-full sm:w-auto">
                {install.isPending && <Loader2 className="animate-spin" />}
                {submitLabel}
              </Button>
            </div>
          </>
        )}
      </form>
      {help && <p className="text-xs text-muted-foreground">{help}</p>}
    </div>
  );
}
