import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { keys } from "./live";
import { projectUrls } from "./urls";

type FlashReply = { ok: boolean; error?: string; messages?: { level: string; text: string }[] };

function form(fields: Record<string, string>) {
  const fd = new FormData();
  for (const [k, v] of Object.entries(fields)) fd.append(k, v);
  return fd;
}

function announce(reply: FlashReply, fallback: string) {
  const text = reply.messages?.map((m) => m.text).join(" ") || fallback;
  toast.success(text);
}

/** Project-level control (pause/resume/run_next/delete) via the flash_json endpoint. */
export function useProjectControl(pk: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (action: "pause" | "resume" | "run_next" | "delete") =>
      api<FlashReply>(projectUrls(pk).control, { form: form({ action }) }).then((r) => ({ r, action })),
    onSuccess: ({ r, action }) => {
      if (action === "delete") {
        window.location.href = "/projects/";
        return;
      }
      announce(r, "Done");
      qc.invalidateQueries({ queryKey: keys.ops(pk) });
    },
    onError: (e: Error) => toast.error(e.message),
  });
}
