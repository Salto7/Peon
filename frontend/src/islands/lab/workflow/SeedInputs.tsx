import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { type SeedInput, TARGET_TYPES, type TargetType } from "./types";

type Props = { seeds: SeedInput[]; onChange: (next: SeedInput[]) => void };

/** What project creation asks the operator for. Reference an answer in any instruction as `{{key}}`. */
export default function SeedInputs({ seeds, onChange }: Props) {
  const update = (i: number, patch: Partial<SeedInput>) => onChange(seeds.map((s, j) => (j === i ? { ...s, ...patch } : s)));
  const add = () =>
    onChange([
      ...seeds,
      { key: `input_${seeds.length + 1}`, label: "", kind: "text", required: false, maps_to: "summary", target_type: "domain" },
    ]);
  return (
    <div className="h-full space-y-2 overflow-y-auto p-3">
      <div className="mb-1 flex items-center justify-between gap-2">
        <p className="text-[11px] text-muted-foreground">
          Asked at project creation. Use <code>{"{{key}}"}</code> in an instruction to place the answer.
        </p>
        <Button size="sm" variant="ghost" onClick={add}>
          <Plus className="size-3.5" /> Add input
        </Button>
      </div>
      <ul className="space-y-2">
        {seeds.map((s, i) => (
          <li key={i} className="space-y-2 rounded-lg border p-2.5">
            <div className="grid grid-cols-[1fr_1.4fr_auto_auto] items-end gap-2">
              <div className="space-y-1">
                <Label className="text-[11px]">Key</Label>
                <Input value={s.key} className="h-8 font-mono text-xs" onChange={(e) => update(i, { key: e.target.value.replace(/[^A-Za-z0-9_-]/g, "") })} />
              </div>
              <div className="space-y-1">
                <Label className="text-[11px]">Question</Label>
                <Input value={s.label} className="h-8 text-xs" placeholder="e.g. Target domain" onChange={(e) => update(i, { label: e.target.value })} />
              </div>
              <div className="flex items-center gap-1.5 pb-1.5">
                <Switch size="sm" checked={s.required} onCheckedChange={(v) => update(i, { required: v })} />
                <span className="text-[11px] text-muted-foreground">required</span>
              </div>
              <Button size="icon" variant="ghost" className="size-7" aria-label="Remove input" onClick={() => onChange(seeds.filter((_, j) => j !== i))}>
                <Trash2 className="size-3.5" />
              </Button>
            </div>
            <div className="flex flex-wrap items-end gap-3">
              <div className="space-y-1">
                <Label className="text-[11px]">Goes to</Label>
                <Select
                  value={s.maps_to}
                  onValueChange={(v) => update(i, { maps_to: v as SeedInput["maps_to"], kind: v === "scope" ? "scope" : "text" })}
                >
                  <SelectTrigger className="h-8 w-36 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="summary">project brief</SelectItem>
                    <SelectItem value="scope">scope (RoE)</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {s.maps_to === "scope" && (
                <div className="space-y-1">
                  <Label className="text-[11px]">Target type</Label>
                  <Select value={s.target_type} onValueChange={(v) => update(i, { target_type: v as TargetType })}>
                    <SelectTrigger className="h-8 w-32 text-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {TARGET_TYPES.map((t) => (
                        <SelectItem key={t} value={t}>
                          {t}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}
              <p className="pb-1.5 text-[11px] text-muted-foreground">
                placeholder: <code>{`{{${s.key || "key"}}}`}</code>
              </p>
            </div>
          </li>
        ))}
        {seeds.length === 0 && <li className="text-xs text-muted-foreground">No inputs — the workflow runs with whatever the brief says.</li>}
      </ul>
    </div>
  );
}
