import { lazy, Suspense, type ComponentProps } from "react";
import { Skeleton } from "@/components/ui/skeleton";

// CodeMirror is ~600 kB; fetch it only when an editor actually renders.
const Editor = lazy(() => import("./code-editor").then((m) => ({ default: m.CodeEditor })));

export type { CodeLanguage } from "./code-editor";

export function CodeEditor(props: ComponentProps<typeof Editor>) {
  return (
    <Suspense fallback={<Skeleton style={{ height: props.minHeight ?? "8rem" }} className="w-full rounded-md" />}>
      <Editor {...props} />
    </Suspense>
  );
}
