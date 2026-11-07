import { FileText, Plus, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import CredentialsPanel, { normalizeCred, type CredRow } from "./CredentialsPanel";

type ScopeItem =
  | { id: string; kind: "target"; value: string }
  | { id: string; kind: "file"; value: string; file: File };

type Props = {
  kinds: { value: string; label: string }[];
};

const uid = () => Math.random().toString(36).slice(2, 10);

export default function CreateScopeCreds({ kinds }: Props) {
  const [scope, setScope] = useState<ScopeItem[]>([]);
  const [draft, setDraft] = useState("");
  const [creds, setCreds] = useState<CredRow[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const ta = document.getElementById("in_scope") as HTMLTextAreaElement | null;
    if (ta) {
      ta.value = scope
        .filter((s): s is ScopeItem & { kind: "target" } => s.kind === "target")
        .map((s) => s.value)
        .join("\n");
    }
    const json = document.getElementById("credentials_json") as HTMLInputElement | null;
    if (json) {
      json.value = JSON.stringify(
        creds
          .map(normalizeCred)
          .filter((c) => c.name.trim() && c.secret.trim())
          .map((c) => ({
            name: c.name.trim(),
            kind: c.kind,
            username: c.username,
            domain: c.domain,
            secret: c.secret,
            notes: c.notes,
          })),
      );
    }
    const input = document.getElementById("uploads") as HTMLInputElement | null;
    if (input) {
      const dt = new DataTransfer();
      for (const s of scope) {
        if (s.kind === "file") dt.items.add(s.file);
      }
      input.files = dt.files;
    }
  }, [scope, creds]);

  const addTarget = () => {
    const v = draft.trim();
    if (!v) return;
    setScope((prev) => [...prev, { id: uid(), kind: "target", value: v }]);
    setDraft("");
  };

  const onFiles = (files: FileList | null) => {
    if (!files?.length) return;
    setScope((prev) => [
      ...prev,
      ...Array.from(files).map((file) => ({
        id: uid(),
        kind: "file" as const,
        value: file.name,
        file,
      })),
    ]);
    if (fileRef.current) fileRef.current.value = "";
  };

  return (
    <div className="space-y-8">
      <textarea
        id="in_scope"
        name="in_scope"
        className="hidden"
        readOnly
        aria-hidden
        tabIndex={-1}
      />
      <input
        id="uploads"
        name="uploads"
        type="file"
        multiple
        className="hidden"
        aria-hidden
        tabIndex={-1}
      />
      <input id="credentials_json" name="credentials_json" type="hidden" />

      <section className="space-y-3" aria-labelledby="create-scope-heading">
        <div>
          <h2 id="create-scope-heading" className="text-sm font-semibold tracking-tight">
            3 · Scope
          </h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Targets and sample files that authorize this engagement. Add lines or attach files.
          </p>
        </div>
        <div className="space-y-3 rounded-xl border bg-card p-4 shadow-sm">
          <ul className="space-y-2">
            {scope.map((item) => (
              <li
                key={item.id}
                className="flex items-center gap-2 rounded-lg border border-border/70 bg-background px-3 py-2"
              >
                {item.kind === "file" ? (
                  <FileText className="size-4 shrink-0 text-muted-foreground" />
                ) : (
                  <span className="size-4 shrink-0 rounded-sm border border-muted-foreground/40" />
                )}
                {item.kind === "target" ? (
                  <input
                    className="ui-input h-8 flex-1 font-mono text-xs"
                    value={item.value}
                    onChange={(e) =>
                      setScope((prev) =>
                        prev.map((s) => (s.id === item.id ? { ...s, value: e.target.value } : s)),
                      )
                    }
                  />
                ) : (
                  <span className="flex-1 truncate font-mono text-xs">{item.value}</span>
                )}
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  aria-label="Remove"
                  onClick={() => setScope((prev) => prev.filter((s) => s.id !== item.id))}
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </li>
            ))}
            {!scope.length ? (
              <li className="rounded-lg border border-dashed px-3 py-6 text-center text-xs text-muted-foreground">
                No scope yet — add a target or attach a file.
              </li>
            ) : null}
          </ul>
          <div className="flex gap-2">
            <input
              className="ui-input flex-1 font-mono text-xs"
              placeholder="domain:app.lab"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addTarget();
                }
              }}
            />
            <Button type="button" variant="outline" size="sm" onClick={addTarget}>
              Add
            </Button>
            <Button
              type="button"
              variant="outline"
              size="icon-sm"
              aria-label="Attach file"
              title="Attach sample file"
              onClick={() => fileRef.current?.click()}
            >
              <Plus className="size-4" />
            </Button>
            <input
              ref={fileRef}
              type="file"
              multiple
              className="hidden"
              onChange={(e) => onFiles(e.target.files)}
            />
          </div>
        </div>
      </section>

      <CredentialsPanel
        kinds={kinds}
        rows={creds}
        onChange={setCreds}
        heading="4 · Credentials"
        description="Optional key store. Tickets/keys/certs support paste or file upload. Secrets are never shown again after create."
      />
    </div>
  );
}
