// Types follow docs/CONTRACT.md field for field. Keep both in sync in the same change.

export type Severity = "critical" | "high" | "medium" | "low";
export type Source = "rules" | "local" | "small" | "large" | "cache";

export interface CostSummary {
  decisions: number;
  model_calls: number;
  cost_usd: number;
  baseline_usd: number;
  cost_inr: number;
  baseline_inr: number;
  savings_pct: number;
  by_source: Record<string, { calls: number; cost_usd: number }>;
  cache_hits: number;
  p50_latency_ms: number | null;
  baseline_model: string;
  usd_inr: number;
}

export interface Yearly {
  runs: number;
  cost_inr: number;
  baseline_inr: number;
}

export interface TierStatus {
  model: string;
  provider: string;
  configured: boolean;
  available: boolean;
}

export type RunnerKind = "docker" | "sim";
export type ServiceState = "running" | "stopped" | "paused" | "unhealthy" | "restarting" | "degraded";
export type FailureType =
  | "crash" | "hang" | "network_cut" | "cpu_throttle" | "traffic_spike" | "compound" | "db_overload" | "latency" | "packet_loss";
export type Confidence = "low" | "medium" | "high";
export type Phase = "idle" | "baseline" | "inject" | "observe" | "restore" | "recover" | "settle" | "done";
export type VoteState = "ok" | "queued" | "error" | "lost" | "pending";

export interface GraphNode {
  id: string;
  role: "web" | "api" | "worker" | "cache" | "queue" | "database" | "other";
  image: string;
  ports: string[];
  healthcheck: boolean;
  restart: string | null;
  stateful: boolean;
  replicas: number;
  depends_on: string[];
  fan_in: number;
  spof: boolean;
  notes: string[];
}
export interface GraphEdge {
  from: string;
  to: string;
  kind: "depends_on" | "dataflow";
  label: string;
}
export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface Experiment {
  id: string;
  type: FailureType;
  target: string;
  title: string;
  description: string;
  params: Record<string, number | string>;
  risk_hint: number;
}

export interface Prediction {
  experiment_id: string;
  summary: string;
  expected_error_pct: number;
  expected_votes_lost: number;
  expected_recovery_s: number;
  confidence: Confidence;
  reason: string;
  hypothesis: string;
  recommended_fault_s?: number;
  decided_by: Source;
  model: string;
}

export interface TimelinePoint {
  t: number;
  ok: number;
  err: number;
  p95_ms: number;
  error_rate: number;
  unhealthy: number;
}

export interface Outcome {
  sent: number;
  acked: number;
  stored: number;
  lost: number;
  errors: number;
  error_pct: number;
  p95_ms: number;
  peak_p95_ms: number;
  recovery_s: number | null;
  availability_pct: number;
  guard_tripped: boolean;
  guard_reason: string | null;
  data_loss: boolean;
}

export interface ExperimentResult {
  run_id: string;
  experiment: Experiment;
  prediction: Prediction | null;
  outcome: Outcome;
  timeline: TimelinePoint[];
  fleet: VoteState[];
  started_at: number;
  duration_s: number;
  label: string;
  prediction_error: { errors_pct_points: number; votes_lost: number; recovery_s: number | null } | null;
  fault_duration_s?: number;
  fault_ended_s?: number | null;
  configuration_signature?: string;
  wall_s?: number | null;
  notes?: string[];
}

export interface Pick {
  experiment_id: string;
  reason: string;
  hypothesis: string;
  decided_by: Source;
  model: string;
  order: number;
}
export interface Finding {
  id: string;
  service: string;
  experiment_id: string;
  severity: Severity;
  title: string;
  evidence: string[];
  impact: string;
  confidence: Confidence;
}
export interface Fix {
  id: string;
  finding_id: string;
  title: string;
  change_type: "compose" | "config" | "code" | "process";
  snippet: string;
  steps: string[];
  expected_score_gain: number;
  auto_applicable: boolean;
}
export interface Score {
  value: number;
  breakdown: { name: string; points: number; max: number; note: string }[];
  services?: { service: string; value: number | null; experiments: number; worst: string | null }[];
}

export interface PlanRow {
  experiment_id: string;
  rank: number;
  priority: number;
  reason: string;
}

export interface ServiceStatus {
  id: string;
  state: ServiceState;
  health: "healthy" | "unhealthy" | "none";
  restarts: number;
  cpu_pct: number | null;
  mem_mb: number | null;
}

export interface Unit {
  singular: string;
  plural: string;
  stored: string;
  lost: string;
}

export interface Target {
  name: string;
  compose_file: string;
  graph: Graph;
  assumptions: string[];
  services: ServiceStatus[];
  unit: Unit;
}

export interface GuardView {
  armed: boolean;
  tripped: boolean;
  reason: string | null;
  thresholds: { error_rate: number; p95_ms: number; unhealthy: number; max_wall_s: number };
  seconds_left: number;
}

export interface Counters {
  sent: number;
  acked: number;
  stored: number;
  lost: number;
  errors: number;
}

export interface Current {
  phases?: Phase[]; // phases this run actually entered (a refused start never shows inject as done)
  run_id: string;
  experiment: Experiment;
  prediction: Prediction | null;
  label: string;
  elapsed_s: number;
  wall_s?: number | null;
  fault_duration_s: number;
  fault_ended_s?: number | null;
  timeline: TimelinePoint[];
  fleet: VoteState[];
  guard: GuardView;
  counters: Counters;
}

export interface Baseline {
  rps: number;
  error_pct: number;
  p95_ms: number;
  healthy_services?: number;
  total_services?: number;
}

export interface State {
  saved_at?: number;
  restored_from?: number | null; // set when runs were reloaded from an earlier session (after a restart)
  target: string;
  runner: { kind: RunnerKind; ready: boolean; detail: string };
  services: ServiceStatus[];
  baseline: Baseline | null;
  phase: Phase;
  busy: boolean;
  current: Current | null;
  results: ExperimentResult[];
  campaign: {
    status: "idle" | "running" | "done" | "stopped";
    strategy: "ai" | "random";
    budget: number;
    picks: Pick[];
    remaining: string[];
  };
  analysis: null | { findings: Finding[]; score: Score; decided_by: Source; model: string };
  fixes: null | { fixes: Fix[]; decided_by: Source; model: string };
  applied_fixes: string[];
  score_history: { label: string; value: number; ts: number }[];
  twin: null | {
    status: "running" | "done"; rows: TwinRow[]; ran_at: number; seconds?: number;
    attempted?: number; completed?: number; failed?: { experiment_id: string; title: string; reason: string | null }[];
  };
  plan: null | { plan: PlanRow[]; decided_by: Source; model: string };
  compare: null | { planner_runs_to_top3: number | null; random_runs_to_top3: number | null; note: string };
  cost: CostSummary;
  error: string | null;
}

export interface TwinRow {
  experiment_id: string;
  title: string;
  target: string;
  type: FailureType;
  penalty: number;
  error_pct: number;
  lost: number;
  recovery_s: number | null;
  guard_tripped: boolean;
}

export interface Health {
  ok: boolean;
  title: string;
  tiers: Record<"local" | "small" | "large", TierStatus>;
  runner: { kind: RunnerKind; ready: boolean; detail: string };
}

export interface LedgerEntry {
  ts: number;
  task: string;
  source: Source;
  model: string;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  baseline_usd: number;
  latency_ms: number;
  cache: string;
  run_id: string;
  accepted: boolean;
}

const BASE = import.meta.env.VITE_API_BASE ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path, { headers: { "Content-Type": "application/json" }, ...init });
  } catch {
    throw new Error("The backend isn't reachable on port 8000. Start it with `python dev.py`.");
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      // body wasn't JSON; keep the status text
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  health: () => request<Health>("/api/health"),
  target: () => request<Target>("/api/target"),
  targets: () => request<{ targets: { name: string; title: string }[]; current: string }>("/api/targets"),
  selectTarget: (name: string) => post<{ ok: boolean; detail: string }>("/api/target/select", { name }),
  startTarget: () => post<{ ok: boolean; detail: string }>("/api/target/start"),
  stopTarget: () => post<{ ok: boolean; detail: string }>("/api/target/stop"),
  catalog: () => request<{ experiments: Experiment[] }>("/api/catalog"),
  baseline: (duration_s?: number) =>
    post<{ rps: number; error_pct: number; p95_ms: number; votes_sent: number; votes_stored: number }>("/api/baseline", {
      duration_s,
    }),
  predict: (experiment_id: string) => post<Prediction>("/api/predict", { experiment_id }),
  run: (experiment_id: string, label?: string, fault_duration_s?: number) =>
    post<{ run_id: string }>("/api/run", { experiment_id, label, fault_duration_s }),
  killswitch: () => post<{ ok: boolean; restored: string[] }>("/api/killswitch"),
  state: () => request<State>("/api/state"),
  analyze: () => post<{ findings: Finding[]; score: Score; decided_by: Source; model: string }>("/api/analyze"),
  fixes: () => post<{ fixes: Fix[]; decided_by: Source; model: string }>("/api/fixes"),
  applyFixes: (fix_ids: string[]) => post<{ ok: boolean; overlay: string; detail: string }>("/api/apply-fixes", { fix_ids }),
  rerun: (experiment_ids?: string[]) => post<{ ok: boolean; experiments: string[] }>("/api/rerun", { experiment_ids }),
  reset: () => post<{ ok: boolean; detail: string }>("/api/reset"),
  startCampaign: (budget: number, strategy: "ai" | "random", seed?: number) =>
    post<{ ok: boolean }>("/api/campaign/start", { budget, strategy, seed }),
  stopCampaign: () => post<{ ok: boolean; restored: string[] }>("/api/campaign/stop"),
  twin: () => post<{ ok: boolean }>("/api/twin"),
  plan: () => post<{ plan: PlanRow[]; decided_by: Source; model: string }>("/api/plan"),
  editEdge: (from: string, to: string, action: "add" | "remove", label?: string) =>
    post<{ ok: boolean; graph: Graph }>("/api/graph/edge", { from, to, action, label }),
  report: () => request<{ markdown: string; score: Score | null; score_history: { label: string; value: number; ts: number }[] }>("/api/report"),
  snapshot: () => request<State>("/api/snapshot"),
  ledger: () => request<{ summary: CostSummary; entries: LedgerEntry[] }>("/api/ledger"),
  resetLedger: () => request<{ ok: boolean }>("/api/ledger/reset", { method: "POST" }),
};
