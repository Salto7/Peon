export type NodeKind = "role" | "skill" | "tool";
export type EdgeKind = "sequence" | "parallel";

export type NodeConfig = {
  title?: string;
  instruction?: string;
  acceptance?: string;
  phase?: string;
  /** Legacy: a node "consumes" a seed input (true). */
  [seedKey: string]: string | boolean | undefined;
};

export type WFNode = {
  id: string;
  kind: NodeKind;
  ref_id: string;
  label: string;
  x: number;
  y: number;
  /** Optional card size on the canvas (defaults applied in layout helpers). */
  w?: number;
  h?: number;
  config: NodeConfig;
};
export type WFEdge = { id: string; source: string; target: string; kind: EdgeKind };
export type Graph = { nodes: WFNode[]; edges: WFEdge[] };
export type LintResult = { errors: string[]; warnings: string[] };

export const TARGET_TYPES = ["domain", "ip", "netblock", "url", "email", "person", "org", "path", "other"] as const;
export type TargetType = (typeof TARGET_TYPES)[number];
export type SeedInput = {
  key: string;
  label: string;
  kind: "text" | "scope";
  required: boolean;
  maps_to: "summary" | "scope";
  target_type: TargetType;
};

export type WorkflowRow = {
  workflow_id: string;
  name: string;
  description: string;
  seed_inputs: SeedInput[];
  graph: Graph;
  lint_errors: LintResult;
  is_published: boolean;
};

export type CatalogOption = {
  id: string;
  label: string;
  description: string;
  /** roles: "manager" | "analyzer" | "specialist" */
  role_kind?: string;
  /** roles: skills bound to it · skills: roles that may use it */
  bound?: string[];
};
export type Catalog = { roles: CatalogOption[]; skills: CatalogOption[]; tools: CatalogOption[] };

export type PlanRow = {
  seq: number;
  title: string;
  role_id: string;
  skill_ids: string[];
  tools: string[];
  depends_on: number[];
  phase: string;
};
export type PlanPreview = { objectives: PlanRow[]; role_ids: string[] };

export const PHASES = ["recon", "initial-access", "post-exploit", "reporting"] as const;

/** What an edge may connect: role→role, role→skill, role→tool, skill→tool. */
export const ALLOWED_LINKS: ReadonlySet<string> = new Set(["role>role", "role>skill", "role>tool", "skill>tool"]);
export const canLink = (from: NodeKind, to: NodeKind) => ALLOWED_LINKS.has(`${from}>${to}`);

export const KIND_LABEL: Record<NodeKind, string> = { role: "Role", skill: "Skill", tool: "Tool" };
