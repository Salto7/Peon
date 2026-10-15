/**
 * The one fetch wrapper for Django endpoints: CSRF header, JSON negotiation and
 * the server's ``{ok, error}`` envelope (see peon/projects/http_helpers.py).
 */
import type { z } from "zod";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly body?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function csrfToken(): string {
  const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
  if (m?.[1]) return decodeURIComponent(m[1]);
  const input = document.querySelector<HTMLInputElement>("input[name=csrfmiddlewaretoken]");
  return input?.value ?? "";
}

type RequestOpts<T> = {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  json?: unknown;
  form?: FormData;
  schema?: z.ZodType<T>;
  signal?: AbortSignal;
};

export async function api<T = unknown>(url: string, opts: RequestOpts<T> = {}): Promise<T> {
  const method = opts.method ?? (opts.json !== undefined || opts.form ? "POST" : "GET");
  const headers: Record<string, string> = {
    Accept: "application/json",
    "X-Requested-With": "XMLHttpRequest",
  };
  let body: BodyInit | undefined;
  if (opts.form) {
    body = opts.form;
  } else if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  }
  if (method !== "GET") headers["X-CSRFToken"] = csrfToken();

  const res = await fetch(url, {
    method,
    headers,
    body,
    credentials: "same-origin",
    signal: opts.signal,
  });
  const text = await res.text();
  let data: unknown = undefined;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      throw new ApiError(`Unexpected non-JSON response (${res.status})`, res.status, text);
    }
  }
  const envelope = data as { ok?: boolean; error?: string } | undefined;
  if (!res.ok || envelope?.ok === false) {
    throw new ApiError(envelope?.error || `Request failed (${res.status})`, res.status, data);
  }
  return opts.schema ? opts.schema.parse(data) : (data as T);
}
