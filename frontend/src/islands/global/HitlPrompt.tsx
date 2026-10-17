import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { z } from "zod";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { subscribe } from "@/lib/sse";

const Pending = z.object({
  id: z.coerce.string(),
  kind: z.string().optional().nullable(),
  question: z.string().optional().nullable(),
  options: z.array(z.string()).optional().nullable(),
  job_title: z.string().optional().nullable(),
  project_id: z.coerce.string().optional().nullable(),
  project_title: z.string().optional().nullable(),
  href: z.string().optional().nullable(),
});
const PendingResponse = z.object({ pending: z.array(Pending) });
type Pending = z.infer<typeof Pending>;
type Decision = "approve" | "deny" | "edit";

const SEEN_KEY = "peon-hitl-seen";

function loadSeen(): Set<string> {
  try {
    return new Set(JSON.parse(sessionStorage.getItem(SEEN_KEY) || "[]") as string[]);
  } catch {
    return new Set();
  }
}

function saveSeen(seen: Set<string>) {
  try {
    sessionStorage.setItem(SEEN_KEY, JSON.stringify([...seen]));
  } catch {
    /* non-essential */
  }
}

/**
 * Cross-project operator prompts. Updates every ``[data-hitl-badge]`` in the
 * server-rendered sidebar and pops a dialog for each prompt not yet seen.
 */
export default function HitlPrompt({
  listUrl,
  decideUrl,
  eventsUrl,
}: {
  listUrl: string;
  decideUrl: string;
  eventsUrl?: string;
}) {
  const qc = useQueryClient();
  const [seen, setSeen] = useState(loadSeen);
  // Reply is tied to the prompt it was typed for, so switching prompts clears it.
  const [draft, setDraft] = useState({ id: "", text: "" });

  const { data } = useQuery({
    queryKey: ["hitl-pending"],
    queryFn: ({ signal }) => api(listUrl, { schema: PendingResponse, signal }),
    // Pushed over SSE when available; polling is the fallback.
    refetchInterval: eventsUrl ? 120_000 : 5_000,
  });

  useEffect(() => {
    if (!eventsUrl) return;
    return subscribe(eventsUrl, {
      pending: (data) => {
        const parsed = PendingResponse.safeParse(data);
        if (parsed.success) qc.setQueryData(["hitl-pending"], parsed.data);
      },
    });
  }, [eventsUrl, qc]);
  const pending = data?.pending ?? [];
  const current = pending.find((p) => !seen.has(p.id)) ?? null;
  const reply = draft.id === current?.id ? draft.text : "";
  const setReply = (text: string) => setDraft({ id: current?.id ?? "", text });

  useEffect(() => {
    document.querySelectorAll<HTMLElement>("[data-hitl-badge]").forEach((el) => {
      el.textContent = String(pending.length);
      el.hidden = pending.length === 0;
    });
  }, [pending.length]);

  const markSeen = (id: string) =>
    setSeen((prev) => {
      const next = new Set(prev).add(id);
      saveSeen(next);
      return next;
    });

  const decide = useMutation({
    mutationFn: (decision: Decision) =>
      api(decideUrl, { json: { id: current?.id, decision, reply } }),
    onSuccess: () => {
      if (current) markSeen(current.id);
      toast.success("Response sent");
      qc.invalidateQueries({ queryKey: ["hitl-pending"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const close = () => {
    if (current) markSeen(current.id);
  };

  // Blocked objectives cannot be answered here; they need the project page.
  const projectOnly = !!current?.id.startsWith("obj-");
  // A multiple-choice question is answered, not approved/denied.
  const options = (current?.options ?? []).filter((o) => o.trim());
  const hasOptions = options.length > 0;

  return (
    <Dialog open={!!current} onOpenChange={(o) => !o && close()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Operator input needed</DialogTitle>
          <DialogDescription>
            {current?.project_title || current?.project_id}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          {current?.kind ? <Badge variant="secondary">{current.kind}</Badge> : null}
          {current?.job_title ? (
            <p className="text-xs text-muted-foreground">Asked by {current.job_title}</p>
          ) : null}
          <p className="text-sm whitespace-pre-wrap">{current?.question}</p>
          {!projectOnly && hasOptions && (
            <div className="flex flex-wrap gap-2" role="group" aria-label="Answer options">
              {options.map((opt) => (
                <Button
                  key={opt}
                  type="button"
                  size="sm"
                  variant={reply === opt ? "default" : "outline"}
                  onClick={() => setReply(opt)}
                >
                  {opt}
                </Button>
              ))}
            </div>
          )}
          {!projectOnly && (
            <div className="space-y-1.5">
              <Label htmlFor="hitl-reply">Reply</Label>
              <Textarea
                id="hitl-reply"
                rows={3}
                value={reply}
                onChange={(e) => setReply(e.target.value)}
                placeholder={
                  hasOptions
                    ? "Pick an option above, or type your own answer"
                    : "Optional for approve/deny, required to send an edit"
                }
              />
            </div>
          )}
        </div>
        <DialogFooter className="gap-2 sm:justify-between">
          <Button variant="ghost" asChild>
            <a href={current?.href ?? "#"}>Open project</a>
          </Button>
          {!projectOnly && (
            <div className="flex gap-2">
              {hasOptions ? (
                <Button
                  disabled={decide.isPending || !reply.trim()}
                  onClick={() => decide.mutate("edit")}
                >
                  Send answer
                </Button>
              ) : (
                <>
                  <Button
                    variant="outline"
                    disabled={decide.isPending}
                    onClick={() => decide.mutate("deny")}
                  >
                    Deny
                  </Button>
                  <Button
                    variant="outline"
                    disabled={decide.isPending || !reply.trim()}
                    onClick={() => decide.mutate("edit")}
                  >
                    Send edit
                  </Button>
                  <Button disabled={decide.isPending} onClick={() => decide.mutate("approve")}>
                    Approve
                  </Button>
                </>
              )}
            </div>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
