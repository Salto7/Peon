import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Trash2 } from "lucide-react";
import { useId, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";

type Relation = {
  id: string;
  skill_id: string;
  related_skill_id: string;
  kind: string;
  note: string;
  conditions?: string;
};

type Props = {
  listUrl: string;
  writeUrl: string;
  deleteUrl: string;
  relationKinds: string[];
  skillIds: string[];
};

const KIND_LABEL: Record<string, string> = {
  sequence: "runs after",
  depends_on: "depends on",
  preferred_over: "preferred over",
  supersedes: "supersedes",
  does_not_run_with: "does not run with",
  runs_under: "runs under",
  overlaps: "overlaps with",
};

const KIND_HELP: Record<string, string> = {
  sequence: "Prefer running the first skill after the second finishes.",
  depends_on: "First skill needs the second present (auto-included when binding).",
  preferred_over: "If both would bind, keep the first and drop the second.",
  supersedes: "First skill replaces the second for the same job.",
  does_not_run_with: "These two must not bind together.",
  runs_under: "First skill is a sub-agent skill under the second.",
  overlaps:
    "Similar purpose — advisory flag. Prefer not to bind both for one job unless you upgrade to preferred over / supersedes. Does not delete either skill.",
};

/**
 * Direct editing of ``CatalogSkillRelation``. Approving a skill_overlap proposal
 * also writes here as ``overlaps``.
 */
export default function SkillRelations({ listUrl, writeUrl, deleteUrl, relationKinds, skillIds }: Props) {
  const ids = useId();
  const qc = useQueryClient();
  const [skillId, setSkillId] = useState(skillIds[0] ?? "");
  const [relatedSkillId, setRelatedSkillId] = useState(skillIds[1] ?? skillIds[0] ?? "");
  const [kind, setKind] = useState(relationKinds.includes("overlaps") ? "overlaps" : relationKinds[0] ?? "sequence");
  const [note, setNote] = useState("");
  const [conditions, setConditions] = useState("");

  const { data } = useQuery({
    queryKey: ["skill-relations"],
    queryFn: () => api<{ relations: Relation[] }>(listUrl).then((r) => r.relations),
  });
  const relations = data ?? [];

  const create = useMutation({
    mutationFn: () =>
      api<{ relation: Relation }>(writeUrl, {
        json: { skill_id: skillId, related_skill_id: relatedSkillId, kind, note, conditions },
      }),
    onSuccess: () => {
      toast.success("Relation saved");
      setNote("");
      setConditions("");
      qc.invalidateQueries({ queryKey: ["skill-relations"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const remove = useMutation({
    mutationFn: (id: string) =>
      api(deleteUrl.replace("{id}", encodeURIComponent(id)), { method: "DELETE", json: {} }),
    onSuccess: () => {
      toast.success("Relation removed");
      qc.invalidateQueries({ queryKey: ["skill-relations"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <div className="min-w-0 space-y-4">
      <p className="text-xs text-pretty text-muted-foreground">
        {KIND_HELP[kind] ?? "How two skills relate for planning and binding."}
      </p>
      <form
        className="grid min-w-0 gap-3 md:grid-cols-3"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <div className="min-w-0 space-y-1.5">
          <Label htmlFor={`${ids}-skill`}>Skill</Label>
          <Select value={skillId} onValueChange={setSkillId}>
            <SelectTrigger id={`${ids}-skill`} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {skillIds.map((id) => (
                <SelectItem key={id} value={id}>
                  {id}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="min-w-0 space-y-1.5">
          <Label htmlFor={`${ids}-kind`}>Relation</Label>
          <Select value={kind} onValueChange={setKind}>
            <SelectTrigger id={`${ids}-kind`} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {relationKinds.map((k) => (
                <SelectItem key={k} value={k}>
                  {KIND_LABEL[k] ?? k}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="min-w-0 space-y-1.5">
          <Label htmlFor={`${ids}-related`}>Related skill</Label>
          <Select value={relatedSkillId} onValueChange={setRelatedSkillId}>
            <SelectTrigger id={`${ids}-related`} className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {skillIds.map((id) => (
                <SelectItem key={id} value={id}>
                  {id}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="min-w-0 space-y-1.5 md:col-span-2">
          <Label htmlFor={`${ids}-conditions`}>Condition (optional)</Label>
          <Input
            id={`${ids}-conditions`}
            value={conditions}
            placeholder="When this relation applies — leave empty = always"
            onChange={(e) => setConditions(e.target.value)}
            className="min-w-0"
          />
        </div>
        <div className="min-w-0 space-y-1.5">
          <Label htmlFor={`${ids}-note`}>Note (optional)</Label>
          <Input id={`${ids}-note`} value={note} onChange={(e) => setNote(e.target.value)} className="min-w-0" />
        </div>
        <div className="flex md:col-span-3 md:justify-end">
          <Button
            type="submit"
            disabled={create.isPending || !skillId || !relatedSkillId || skillId === relatedSkillId}
            className="w-full sm:w-auto"
          >
            {create.isPending && <Loader2 className="animate-spin" />}
            Save relation
          </Button>
        </div>
      </form>
      {relations.length > 0 && (
        <ul className="divide-y overflow-hidden rounded-lg border text-sm">
          {relations.map((r) => (
            <li key={r.id} className="flex items-start justify-between gap-3 px-3 py-2.5">
              <span className="min-w-0 flex-1 break-words font-mono text-[13px] leading-relaxed">
                {r.skill_id}{" "}
                <span className="text-muted-foreground">{KIND_LABEL[r.kind] ?? r.kind}</span>{" "}
                {r.related_skill_id}
                {r.conditions ? (
                  <span className="mt-0.5 block text-muted-foreground">when {r.conditions}</span>
                ) : null}
                {r.note ? <span className="mt-0.5 block text-muted-foreground">— {r.note}</span> : null}
              </span>
              <Button
                size="icon"
                variant="ghost"
                className="shrink-0"
                disabled={remove.isPending}
                onClick={() => remove.mutate(r.id)}
                aria-label="Remove relation"
              >
                <Trash2 className="size-4" />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
