/** Shapes of the project console endpoints (jobs.json, messages, findings.json). */

export type Agent = {
  id: string;
  title: string;
  status: string;
  role_ids?: string[];
  skill_ids?: string[];
  primary_role_id?: string;
  role_label?: string;
  crew_role?: string;
  role_goal?: string;
  role?: string;
  lane?: string;
  is_spawn?: boolean;
  terminal?: boolean;
  error?: string;
  description?: string;
  operator_command?: string;
  last_command?: string;
  commands?: string[];
  tool_calls?: number;
  objective_seq?: number | null;
  objective_title?: string;
  objective_description?: string;
  objective_acceptance?: string;
  objective_phase?: string;
  updated_at?: string;
  subagents?: Agent[];
};

export type Objective = {
  id: string;
  seq: number;
  title: string;
  phase: string;
  description?: string;
  acceptance_criteria?: string;
  role_id?: string;
  status: string;
};

export type Report = {
  name: string;
  relative_path: string;
  display_name: string;
  size: number;
  modified_at?: string | null;
  kind: string;
  is_markdown: boolean;
};

export type PendingInput = { id: string; question?: string; job_title?: string; kind?: string };

export type SurfaceSource = { name: string; first_seen: string; last_seen: string; count: number };

export type SurfaceNode = {
  id: string;
  label: string;
  type: string;
  /** Open Asset Model class slug (fqdn, ip, netblock, …) and its display fields. */
  class?: string;
  class_label?: string;
  oam?: string;
  group?: string;
  bucket: string;
  kind?: string;
  center?: boolean;
  value?: string;
  source?: string;
  sources?: SurfaceSource[];
  first_seen?: string;
  last_seen?: string;
  title?: string;
  severity?: string;
  status?: string;
  finding_count?: number;
  degree?: number;
  parent_id?: string;
  props?: Record<string, unknown>;
};
export type SurfaceEdge = {
  source: string;
  target: string;
  rel: string;
  title?: string;
  /** ok | unexpected (known label, odd endpoints) | custom (unregistered label) */
  status?: "ok" | "unexpected" | "custom";
  derived?: boolean;
};
export type AttackSurface = {
  nodes: SurfaceNode[];
  edges: SurfaceEdge[];
  legend?: Record<string, unknown>;
  stats?: { nodes: number; edges: number };
};

export type RoleSummary = { name: string; description?: string; tags?: string[]; category?: string };

export type Ops = {
  project: {
    status: string;
    completed_at?: string | null;
    phase?: string;
    sandbox?: { name?: string; mode?: string };
    progress?: {
      objectives_done: number;
      objectives_total: number;
      jobs_done: number;
      jobs_total: number;
      ratio: number;
    };
  };
  agents: Agent[];
  roles_used: RoleSummary[];
  objectives: Objective[];
  finding_counts: { all: number; open: number; confirmed: number };
  attack_surface: AttackSurface;
  reports: Report[];
  next_objective_ready: boolean;
  pending_inputs: PendingInput[];
  command_hints?: { cli?: string[]; capabilities?: string[] };
};

export type StreamMessage = {
  id: number | string;
  message_type: string;
  content: string;
  metadata: Record<string, unknown>;
  created_at: string;
  job_id: string;
  job_title: string;
  job_status?: string;
};

export type Finding = {
  id: string;
  seq: number;
  title: string;
  kind: string;
  asset_type: string;
  severity: string;
  status: string;
  host: string;
  service: string;
  port: number | null;
  protocol: string;
  cve_id: string;
  cwe_id: string;
  mitre_techniques: string[];
  description: string;
  evidence: string;
  evidence_path: string;
  remediation: string;
  severity_original?: string;
};

export type FindingsResponse = {
  findings: Finding[];
  counts: { all: number; open: number; confirmed: number };
};

/** Everything the server renders into the page for the console islands. */
export type ProjectContext = {
  pk: string;
  title: string;
  kind: string;
  summary: string;
  runtime: string;
  planUrl?: string;
  findingStatuses: [string, string][];
  findingSeverities: [string, string][];
};
