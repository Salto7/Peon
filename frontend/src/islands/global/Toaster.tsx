import { useEffect } from "react";
import { toast } from "sonner";
import { Toaster as SonnerToaster } from "@/components/ui/sonner";

type FlashMessage = { level: string; text: string };

/** Global toaster; replays Django ``messages`` passed in as props. */
export default function Toaster({ messages = [] }: { messages?: FlashMessage[] }) {
  useEffect(() => {
    for (const m of messages) {
      const fn =
        m.level.includes("error")
          ? toast.error
          : m.level.includes("warning")
            ? toast.warning
            : m.level.includes("success")
              ? toast.success
              : toast.info;
      fn(m.text);
    }
  }, [messages]);
  return <SonnerToaster position="bottom-right" richColors closeButton />;
}
