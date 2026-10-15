import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { subscribe } from "@/lib/sse";
import type { FindingsResponse, Ops, StreamMessage } from "./types";
import { projectUrls } from "./urls";

/** Messages kept in memory per project; older lines drop off the activity feed. */
const MESSAGE_CAP = 1500;

export const keys = {
  ops: (pk: string) => ["ops", pk] as const,
  messages: (pk: string) => ["messages", pk] as const,
  findings: (pk: string, status: string, severity: string) => ["findings", pk, status, severity] as const,
};

export function mergeMessages(prev: StreamMessage[] | undefined, incoming: StreamMessage[]) {
  const list = prev ? [...prev] : [];
  const seen = new Set(list.map((m) => String(m.id)));
  for (const m of incoming) if (!seen.has(String(m.id))) list.push(m);
  return list.length > MESSAGE_CAP ? list.slice(-MESSAGE_CAP) : list;
}

/** Map server invalidation keys onto the queries that render them. */
function invalidate(qc: QueryClient, pk: string, changed: string[]) {
  const opsKeys = ["jobs", "objectives", "project", "assets", "prompts", "findings"];
  if (changed.some((k) => opsKeys.includes(k))) qc.invalidateQueries({ queryKey: keys.ops(pk) });
  if (changed.includes("findings")) qc.invalidateQueries({ queryKey: ["findings", pk] });
}

/**
 * Subscribe the page to ``/projects/<pk>/events/``. Safe to call from several
 * islands: they share one EventSource. Returns whether the stream is live.
 */
export function useLiveProject(pk: string) {
  const qc = useQueryClient();
  const [live, setLive] = useState(false);
  useEffect(
    () =>
      subscribe(
        projectUrls(pk).events,
        {
          messages: (rows) =>
            qc.setQueryData<StreamMessage[]>(keys.messages(pk), (prev) =>
              mergeMessages(prev, rows as StreamMessage[]),
            ),
          invalidate: (data) => invalidate(qc, pk, (data as { keys?: string[] }).keys ?? []),
        },
        setLive,
      ),
    [pk, qc],
  );
  return live;
}

export function useOps(pk: string, initial?: Ops) {
  return useQuery({
    queryKey: keys.ops(pk),
    queryFn: ({ signal }) => api<Ops>(projectUrls(pk).ops, { signal }),
    initialData: initial,
    // Pushed invalidations drive refreshes; this is only a safety net.
    refetchInterval: 60_000,
  });
}

export function useMessages(pk: string, bootstrap: StreamMessage[] = []) {
  return useQuery({
    queryKey: keys.messages(pk),
    queryFn: () => bootstrap,
    initialData: bootstrap,
    staleTime: Infinity,
    gcTime: Infinity,
  }).data;
}

export function useFindings(pk: string, status: string, severity: string) {
  return useQuery({
    queryKey: keys.findings(pk, status, severity),
    queryFn: ({ signal }) =>
      api<FindingsResponse>(
        `${projectUrls(pk).findings}?finding_status=${encodeURIComponent(status)}&finding_severity=${encodeURIComponent(severity)}`,
        { signal },
      ),
    placeholderData: (prev) => prev,
  });
}
