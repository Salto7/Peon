import { useState } from "react";
import { csrfToken } from "@/lib/api";
import CredentialsPanel, { type CredKind, type CredRow } from "./CredentialsPanel";

type Props = {
  actionUrl: string;
  kinds: { value: string; label: string }[];
  credentials?: {
    name: string;
    kind: string;
    username?: string;
    domain?: string;
    notes?: string;
    is_set?: boolean;
  }[];
};

function toRows(credentials: Props["credentials"]): CredRow[] {
  return (credentials || []).map((c) => ({
    id: c.name,
    kind: (c.kind || "password") as CredKind,
    name: c.name,
    username: c.username || "",
    domain: c.domain || "",
    secret: "",
    notes: c.notes || "",
    isSet: !!c.is_set,
  }));
}

async function postForm(actionUrl: string, fields: Record<string, string>) {
  const body = new URLSearchParams();
  body.set("csrfmiddlewaretoken", csrfToken());
  for (const [k, v] of Object.entries(fields)) body.set(k, v);
  const res = await fetch(actionUrl, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "X-CSRFToken": csrfToken(),
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body,
    credentials: "same-origin",
  });
  if (!res.ok) {
    let msg = `Save failed (${res.status})`;
    try {
      const data = (await res.json()) as { error?: string };
      if (data.error) msg = data.error;
    } catch {
      /* ignore */
    }
    throw new Error(msg);
  }
}

export default function KeystoreEditor({ actionUrl, kinds, credentials }: Props) {
  const [rows, setRows] = useState(() => toRows(credentials));

  return (
    <CredentialsPanel
      kinds={kinds}
      rows={rows}
      onChange={setRows}
      heading="Key store"
      description="Credentials any agent in this project can use. Edit to fix mistakes or rotate secrets — blank secret keeps the stored value."
      onSaveRemote={async (row) => {
        await postForm(actionUrl, {
          action: "save",
          name: row.name.trim(),
          kind: row.kind,
          username: row.username,
          domain: row.domain,
          secret: row.secret,
          notes: row.notes,
        });
        setRows((prev) => {
          const next = prev.filter((r) => r.name !== row.name.trim());
          return [
            ...next,
            {
              ...row,
              id: row.name.trim(),
              name: row.name.trim(),
              secret: "",
              isSet: row.isSet || !!row.secret,
            },
          ].sort((a, b) => a.name.localeCompare(b.name));
        });
      }}
      onDeleteRemote={async (name) => {
        await postForm(actionUrl, { action: "delete", name });
        setRows((prev) => prev.filter((r) => r.name !== name));
      }}
    />
  );
}
