import { FileUp, Plus, Trash2, X } from "lucide-react";
import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type CredKind =
  "password" | "ntlm_hash" | "kerberos_ticket" | "ssh_key" | "api_token" | "certificate" | "other";

export type CredRow = {
  id: string;
  kind: CredKind;
  name: string;
  username: string;
  domain: string;
  secret: string;
  notes: string;
  isSet?: boolean;
};

type KindOpt = { value: string; label: string };

const FILE_KINDS = new Set<CredKind>([
  "kerberos_ticket",
  "ssh_key",
  "certificate",
  "ntlm_hash",
  "other",
]);

/** Credentials kind uses username as the handle — no separate name. */
function usesUsernameAsName(kind: CredKind): boolean {
  return kind === "password" || kind === "ntlm_hash";
}

function fieldsFor(kind: CredKind): {
  identityLabel: string;
  password?: boolean;
  domain?: boolean;
  notes?: boolean;
  secretLabel: string;
  fileCapable: boolean;
  secretMultiline: boolean;
} {
  const fileCapable = FILE_KINDS.has(kind);
  switch (kind) {
    case "password":
      return {
        identityLabel: "Username",
        password: true,
        domain: true,
        notes: true,
        secretLabel: "Password",
        fileCapable: false,
        secretMultiline: false,
      };
    case "ntlm_hash":
      return {
        identityLabel: "Username",
        password: true,
        domain: true,
        secretLabel: "NTLM hash",
        fileCapable,
        secretMultiline: false,
      };
    case "kerberos_ticket":
      return {
        identityLabel: "Label",
        password: true,
        domain: true,
        notes: true,
        secretLabel: "Ticket / ccache",
        fileCapable,
        secretMultiline: true,
      };
    case "ssh_key":
      return {
        identityLabel: "Label",
        password: true,
        notes: true,
        secretLabel: "Private key",
        fileCapable,
        secretMultiline: true,
      };
    case "api_token":
      return {
        identityLabel: "Label",
        password: true,
        notes: true,
        secretLabel: "Token",
        fileCapable: false,
        secretMultiline: false,
      };
    case "certificate":
      return {
        identityLabel: "Label",
        password: true,
        notes: true,
        secretLabel: "Certificate / PEM",
        fileCapable,
        secretMultiline: true,
      };
    default:
      return {
        identityLabel: "Label",
        password: true,
        domain: true,
        notes: true,
        secretLabel: "Secret",
        fileCapable,
        secretMultiline: true,
      };
  }
}

function emptyCred(kind: CredKind = "password"): CredRow {
  return {
    id: Math.random().toString(36).slice(2, 10),
    kind,
    name: "",
    username: "",
    domain: "",
    secret: "",
    notes: "",
  };
}

function kindLabel(kinds: KindOpt[], kind: string): string {
  return kinds.find((k) => k.value === kind)?.label || kind;
}

function displayName(row: CredRow): string {
  if (row.name.trim()) return row.name.trim();
  if (row.username.trim()) {
    return row.domain.trim() ? `${row.domain}\\${row.username}` : row.username.trim();
  }
  return "(unnamed)";
}

/** Normalize before save: credentials kinds store username as name. */
export function normalizeCred(row: CredRow): CredRow {
  if (usesUsernameAsName(row.kind)) {
    const user = row.username.trim() || row.name.trim();
    const name = row.domain.trim() ? `${row.domain.trim()}\\${user}` : user;
    return { ...row, username: user, name };
  }
  const name = row.name.trim() || row.username.trim();
  return { ...row, name, username: row.username.trim() };
}

async function fileToSecret(file: File): Promise<string> {
  const buf = await file.arrayBuffer();
  const bytes = new Uint8Array(buf);
  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    if (!text.includes("\0")) return text;
  } catch {
    /* binary */
  }
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

type Props = {
  kinds: KindOpt[];
  rows: CredRow[];
  onChange: (rows: CredRow[]) => void;
  onDeleteRemote?: (name: string) => void | Promise<void>;
  onSaveRemote?: (row: CredRow) => void | Promise<void>;
  heading?: string;
  description?: string;
  className?: string;
};

export default function CredentialsPanel({
  kinds,
  rows,
  onChange,
  onDeleteRemote,
  onSaveRemote,
  heading,
  description,
  className,
}: Props) {
  const [editing, setEditing] = useState<CredRow | null>(null);
  const [secretMode, setSecretMode] = useState<"paste" | "upload">("paste");
  const [busy, setBusy] = useState(false);
  const [fileName, setFileName] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const kindOpts = kinds.length ? kinds : [{ value: "password", label: "Credentials" }];

  const startAdd = () => {
    setEditing(emptyCred((kindOpts[0]?.value as CredKind) || "password"));
    setSecretMode("paste");
    setFileName("");
  };

  const startEdit = (row: CredRow) => {
    const next = { ...row, secret: "" };
    if (usesUsernameAsName(row.kind) && !row.username && row.name) {
      // Split DOMAIN\user if present
      const m = row.name.match(/^([^\\]+)\\(.+)$/);
      if (m) {
        next.domain = m[1] || "";
        next.username = m[2] || "";
      } else {
        next.username = row.name;
      }
    }
    setEditing(next);
    setSecretMode("paste");
    setFileName("");
  };

  const ff = editing ? fieldsFor(editing.kind) : null;

  const identityValue = (row: CredRow) => (usesUsernameAsName(row.kind) ? row.username : row.name);

  const setIdentity = (value: string) => {
    setEditing((f) => {
      if (!f) return f;
      if (usesUsernameAsName(f.kind)) return { ...f, username: value, name: value };
      return { ...f, name: value };
    });
  };

  const canSave = () => {
    if (!editing) return false;
    return !!identityValue(editing).trim();
  };

  const commitLocal = (row: CredRow) => {
    const norm = normalizeCred(row);
    const i = rows.findIndex((r) => r.id === row.id || r.name === norm.name);
    if (i >= 0) {
      const next = [...rows];
      next[i] = norm;
      onChange(next);
    } else {
      onChange([...rows, norm]);
    }
  };

  const save = async () => {
    if (!editing || !canSave()) return;
    const norm = normalizeCred(editing);
    setBusy(true);
    try {
      if (onSaveRemote) await onSaveRemote(norm);
      else commitLocal(norm);
      setEditing(null);
      setFileName("");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (row: CredRow) => {
    if (onDeleteRemote && row.name) {
      if (!confirm(`Delete ${displayName(row)}?`)) return;
      await onDeleteRemote(row.name);
      return;
    }
    onChange(rows.filter((r) => r.id !== row.id));
  };

  const onUpload = async (files: FileList | null) => {
    const file = files?.[0];
    if (!file || !editing) return;
    const secret = await fileToSecret(file);
    setEditing({ ...editing, secret });
    setFileName(file.name);
    if (fileRef.current) fileRef.current.value = "";
  };

  return (
    <div className={cn("space-y-3", className)}>
      {(heading || description) && (
        <div>
          {heading ? <h2 className="text-sm font-semibold tracking-tight">{heading}</h2> : null}
          {description ? (
            <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
          ) : null}
        </div>
      )}

      <div className="space-y-3 rounded-xl border bg-card p-4 shadow-sm">
        <div className="flex items-center justify-between gap-2">
          <p className="text-xs text-muted-foreground">
            {rows.length ? `${rows.length} stored` : "None yet"}
          </p>
          {!editing ? (
            <Button type="button" variant="outline" size="sm" onClick={startAdd}>
              <Plus className="size-3.5" /> Add
            </Button>
          ) : null}
        </div>

        <ul className="space-y-2">
          {rows.map((c) => (
            <li
              key={c.id}
              className="flex items-center gap-3 rounded-lg border border-border/70 bg-background px-3 py-2.5"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate font-mono text-sm font-medium">{displayName(c)}</p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {kindLabel(kindOpts, c.kind)}
                </p>
              </div>
              {c.isSet ? (
                <span className="rounded-md border border-success/40 bg-success/10 px-1.5 py-0.5 text-[10px] text-success">
                  set
                </span>
              ) : null}
              <span className="flex shrink-0 gap-1">
                <Button type="button" variant="ghost" size="sm" onClick={() => startEdit(c)}>
                  Edit
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  aria-label="Remove"
                  onClick={() => void remove(c)}
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </span>
            </li>
          ))}
          {!rows.length && !editing ? (
            <li className="rounded-lg border border-dashed px-3 py-6 text-center text-xs text-muted-foreground">
              No credentials yet.
            </li>
          ) : null}
        </ul>

        {editing && ff ? (
          <div className="space-y-3 rounded-lg border bg-muted/20 p-3">
            <div
              role="tablist"
              aria-label="Credential kind"
              className="flex flex-wrap gap-1 rounded-lg border bg-muted/60 p-1"
            >
              {kindOpts.map((k) => (
                <button
                  key={k.value}
                  type="button"
                  role="tab"
                  aria-selected={editing.kind === k.value}
                  onClick={() => {
                    const kind = k.value as CredKind;
                    setEditing((f) => (f ? { ...f, kind } : f));
                    if (!fieldsFor(kind).fileCapable) setSecretMode("paste");
                    setFileName("");
                  }}
                  className={cn(
                    "h-7 cursor-pointer rounded-md px-2.5 text-xs font-medium text-muted-foreground",
                    "aria-selected:bg-background aria-selected:text-foreground aria-selected:shadow-sm",
                  )}
                >
                  {k.label}
                </button>
              ))}
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <label className="space-y-1 text-xs">
                <span className="font-medium">{ff.identityLabel}</span>
                <input
                  className="ui-input font-mono"
                  value={identityValue(editing)}
                  onChange={(e) => setIdentity(e.target.value)}
                  placeholder={ff.identityLabel === "Username" ? "alice" : "svc-sql01"}
                  autoComplete="off"
                />
              </label>
              {ff.domain ? (
                <label className="space-y-1 text-xs">
                  <span className="font-medium">
                    Domain <span className="text-muted-foreground">(optional)</span>
                  </span>
                  <input
                    className="ui-input"
                    value={editing.domain}
                    onChange={(e) => setEditing((f) => (f ? { ...f, domain: e.target.value } : f))}
                    autoComplete="off"
                  />
                </label>
              ) : null}

              {ff.password ? (
                <div className={cn("space-y-2", ff.secretMultiline ? "sm:col-span-2" : "")}>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-xs font-medium">{ff.secretLabel}</span>
                    {ff.fileCapable ? (
                      <div
                        role="tablist"
                        aria-label="Secret input mode"
                        className="inline-flex rounded-lg border bg-muted p-0.5"
                      >
                        <button
                          type="button"
                          role="tab"
                          aria-selected={secretMode === "paste"}
                          onClick={() => setSecretMode("paste")}
                          className="h-7 cursor-pointer rounded-md px-3 text-xs font-medium text-muted-foreground aria-selected:bg-background aria-selected:text-foreground aria-selected:shadow-sm"
                        >
                          Paste
                        </button>
                        <button
                          type="button"
                          role="tab"
                          aria-selected={secretMode === "upload"}
                          onClick={() => setSecretMode("upload")}
                          className="h-7 cursor-pointer rounded-md px-3 text-xs font-medium text-muted-foreground aria-selected:bg-background aria-selected:text-foreground aria-selected:shadow-sm"
                        >
                          <FileUp className="mr-1.5 inline size-3.5" /> File
                        </button>
                      </div>
                    ) : null}
                  </div>
                  {secretMode === "upload" && ff.fileCapable ? (
                    <div className="space-y-2">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => fileRef.current?.click()}
                      >
                        <FileUp className="size-3.5" /> Choose file
                      </Button>
                      <input
                        ref={fileRef}
                        type="file"
                        className="hidden"
                        onChange={(e) => void onUpload(e.target.files)}
                      />
                      <p className="text-xs text-muted-foreground">
                        {fileName
                          ? `Loaded ${fileName}`
                          : editing.isSet
                            ? "Upload replaces the stored secret."
                            : "Pick a ticket, key, or PEM file."}
                      </p>
                      {editing.secret && ff.secretMultiline ? (
                        <textarea
                          className="ui-textarea font-mono text-xs"
                          rows={3}
                          value={editing.secret}
                          onChange={(e) =>
                            setEditing((f) => (f ? { ...f, secret: e.target.value } : f))
                          }
                        />
                      ) : null}
                    </div>
                  ) : ff.secretMultiline ? (
                    <textarea
                      className="ui-textarea font-mono text-xs"
                      rows={3}
                      value={editing.secret}
                      onChange={(e) =>
                        setEditing((f) => (f ? { ...f, secret: e.target.value } : f))
                      }
                      placeholder={
                        editing.isSet ? "Leave blank to keep stored value" : ff.secretLabel
                      }
                      autoComplete="new-password"
                    />
                  ) : (
                    <input
                      className="ui-input font-mono"
                      type="password"
                      value={editing.secret}
                      onChange={(e) =>
                        setEditing((f) => (f ? { ...f, secret: e.target.value } : f))
                      }
                      placeholder={
                        editing.isSet ? "Leave blank to keep stored value" : ff.secretLabel
                      }
                      autoComplete="new-password"
                    />
                  )}
                </div>
              ) : null}

              {ff.notes ? (
                <label className="space-y-1 text-xs sm:col-span-2">
                  <span className="font-medium">
                    Context <span className="text-muted-foreground">(optional)</span>
                  </span>
                  <input
                    className="ui-input"
                    value={editing.notes}
                    onChange={(e) => setEditing((f) => (f ? { ...f, notes: e.target.value } : f))}
                    placeholder="Where this applies"
                  />
                </label>
              ) : null}
            </div>

            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => {
                  setEditing(null);
                  setFileName("");
                }}
              >
                <X className="size-3.5" /> Cancel
              </Button>
              <Button
                type="button"
                size="sm"
                onClick={() => void save()}
                disabled={!canSave() || busy}
              >
                {busy ? "Saving…" : "Save"}
              </Button>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
