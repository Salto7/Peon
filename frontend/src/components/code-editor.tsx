import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { yaml } from "@codemirror/lang-yaml";
import { HighlightStyle, StreamLanguage, syntaxHighlighting } from "@codemirror/language";
import { shell } from "@codemirror/legacy-modes/mode/shell";
import { EditorView } from "@codemirror/view";
import { tags as t } from "@lezer/highlight";
import CodeMirror, { type Extension } from "@uiw/react-codemirror";
import { useMemo } from "react";
import { cn } from "@/lib/utils";

export type CodeLanguage = "yaml" | "markdown" | "json" | "shell" | "text";

const languages: Record<CodeLanguage, () => Extension[]> = {
  yaml: () => [yaml()],
  markdown: () => [markdown()],
  json: () => [json()],
  shell: () => [StreamLanguage.define(shell)],
  text: () => [],
};

// Colours come from the design tokens so the editor follows the page theme.
const theme = EditorView.theme({
  "&": { backgroundColor: "transparent", color: "var(--foreground)", fontSize: "12.5px" },
  ".cm-content": { fontFamily: "\"Geist Mono Variable\", ui-monospace, monospace", caretColor: "var(--foreground)", padding: "8px 0" },
  ".cm-gutters": {
    backgroundColor: "var(--muted)",
    color: "var(--muted-foreground)",
    borderRight: "1px solid var(--border)",
  },
  ".cm-activeLine": { backgroundColor: "color-mix(in oklab, var(--accent) 60%, transparent)" },
  ".cm-activeLineGutter": { backgroundColor: "var(--accent)" },
  "&.cm-focused": { outline: "none" },
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground, ::selection": {
    backgroundColor: "color-mix(in oklab, var(--primary) 30%, transparent) !important",
  },
  ".cm-cursor": { borderLeftColor: "var(--foreground)" },
});

const highlight = syntaxHighlighting(
  HighlightStyle.define([
    { tag: [t.keyword, t.operatorKeyword, t.definitionKeyword], color: "var(--link)" },
    { tag: [t.string, t.special(t.string)], color: "var(--success)" },
    { tag: [t.number, t.bool, t.null, t.atom], color: "var(--warning)" },
    { tag: [t.propertyName, t.attributeName, t.definition(t.propertyName)], color: "var(--info)" },
    { tag: [t.comment, t.meta], color: "var(--muted-foreground)", fontStyle: "italic" },
    { tag: [t.heading], color: "var(--foreground)", fontWeight: "600" },
    { tag: [t.link, t.url], color: "var(--link)", textDecoration: "underline" },
    { tag: [t.emphasis], fontStyle: "italic" },
    { tag: [t.strong], fontWeight: "600" },
  ]),
);

type Props = {
  value: string;
  onChange?: (value: string) => void;
  language?: CodeLanguage;
  readOnly?: boolean;
  minHeight?: string;
  maxHeight?: string;
  className?: string;
  "aria-label"?: string;
  id?: string;
};

/** CodeMirror 6 editor styled as a form control. */
export function CodeEditor({
  value,
  onChange,
  language = "text",
  readOnly,
  minHeight = "8rem",
  maxHeight = "60vh",
  className,
  id,
  ...rest
}: Props) {
  const extensions = useMemo(
    () => [theme, highlight, EditorView.lineWrapping, ...languages[language]()],
    [language],
  );
  return (
    <div
      id={id}
      className={cn(
        "overflow-hidden rounded-md border border-input shadow-sm focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/40",
        className,
      )}
    >
      <CodeMirror
        value={value}
        onChange={onChange}
        readOnly={readOnly}
        theme="none"
        extensions={extensions}
        minHeight={minHeight}
        maxHeight={maxHeight}
        basicSetup={{ foldGutter: false, highlightActiveLine: !readOnly, autocompletion: false }}
        aria-label={rest["aria-label"]}
      />
    </div>
  );
}
