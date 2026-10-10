// Types follow docs/CONTRACT.md. Keep both in sync. Only the parts the skeleton uses are here so far.

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

export interface Health {
  ok: boolean;
  title: string;
  tiers: Record<"local" | "small" | "large", TierStatus>;
  runner: { kind: "docker" | "sim"; ready: boolean; detail: string };
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

export const api = {
  health: () => request<Health>("/api/health"),
  ledger: () => request<{ summary: CostSummary; entries: LedgerEntry[] }>("/api/ledger"),
  resetLedger: () => request<{ ok: boolean }>("/api/ledger/reset", { method: "POST" }),
};
