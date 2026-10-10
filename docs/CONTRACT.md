# Chaos Sentinel: build contract (backend and frontend must both follow this)

## Product in one paragraph
An AI that stress-tests a containerised multi-service app by breaking it on purpose, safely. It maps the
app, predicts what each failure will do, injects it behind a deterministic kill switch, measures the real
impact, then ranks weaknesses, recommends fixes, and proves the fix by re-running the same experiment.
Default target: Docker's example-voting-app (vote, redis, worker, db, result), run with Docker Compose on a
laptop. The tool reads any compose file, so other targets work with reduced business checks.

The Bosch hackathon asks for: dependency map; baseline monitoring; at least 3 failure types; an AI experiment
planner that ranks experiments with a reason for each; safety limits with automatic stop or rollback;
errors, slowdowns and recovery time recorded; AI explanation of findings, ranked by severity, with fixes and
ideally a resilience score. Everything below serves that. "AI proposes, deterministic guard disposes."

## Ground truth about the default target (read from the real repo)
- vote (Flask, port 8080 on host): POST / with form `vote=a|b` and cookie `voter_id`. It does
  `redis.rpush('votes', ...)` with socket_timeout=5 and NO error handling, so Redis down means HTTP 500.
- redis (redis:alpine): holds the queue list `votes`. Killing it loses whatever is queued.
- worker (.NET): pops from redis every ~100 ms and upserts into Postgres. It reconnects to redis and db by itself.
- db (postgres:15-alpine, user/pass postgres/postgres): table `votes(id varchar unique, vote varchar)`; `id` is the voter_id.
- result (Node, port 8081): reads Postgres, pushes counts over socket.io.
- compose has healthchecks on vote, redis, db and `depends_on: condition: service_healthy`, but NO `restart:` policy
  on anything, so a killed container stays dead. No service has more than one replica.
- Business invariant ("votes lost"): the probe posts votes with unique voter ids `cs-<run>-<n>`; a vote is
  `stored` when `SELECT count(*) FROM votes WHERE id LIKE 'cs-%'` includes it (check ids, not just a count). A vote that
  got HTTP 200 but is never stored after a settle period is `lost` (silent data loss). HTTP 5xx or timeout is `error`.
- Compound weakness worth finding: pause or kill `worker`, votes keep returning 200 and pile up in Redis, then kill
  `redis`: silent loss of acknowledged votes.

## Layout and ownership
```
backend/app/        (owned by the BACKEND builder)
frontend/src/       (owned by the FRONTEND builder)
docs/CONTRACT.md    this file (owned by the lead; ask before changing)
targets/            target apps live here (the voting app is cloned to targets/voting by the user)
```
Reuse from the existing kit without rewriting: `backend/app/llm.py` (model router), `ledger.py`, `cache.py`, `config.py`,
and `frontend/src/components/CostGauge.tsx`, `TierStatus.tsx`, `views/LedgerView.tsx`, `lib/format.ts`, `index.css` tokens.

## Targets and units
`TARGET=voting|hawkbit|generic` selects the adapter (docs/TARGETS.md). The fleet grid, counters and Outcome fields (`sent`, `acked`, `stored`,
`lost`) describe generic "units": votes for the voting app, devices for hawkBit, plain requests for `generic`. Field names stay as written
(they are not renamed per target); only the labels from `/target`.`unit` change. Everywhere below, "votes" in an example means "units".

## Command line (CI gate)
`python -m app.cli gate --min-score 80 [--budget 5] [--strategy ai|random]` starts the target (or simulation), runs a campaign, prints the findings
and score, writes the report to `reports/`, and exits 0 when score >= min-score and 1 otherwise (2 on setup errors). An example GitHub Actions
workflow lives in docs/DEMO.md or `.github/workflows/chaos-gate.yml`.

## Runtime modes
`RUNNER=docker|sim|auto` (env). `auto` = docker if the Docker daemon answers and the compose project at `TARGET_DIR`
(default `targets/voting`) exists; otherwise `sim`. `sim` is a faithful in-memory simulation of the voting app
(service states, queueing in redis, worker drain, 500s when redis is down, reconnects, restart policies) so the
whole product demos and tests with no Docker. The UI must show a clear "Simulation" badge in sim mode and "Docker" otherwise.

## Defaults
- probe rate: 5 votes/s (one fresh voter per vote; `PROBE_RATE` overrides). Not 10: the worker pops one vote per ~100 ms
  (worker/Program.cs), so at 10/s its queue can never drain after a fault. The fleet grid shows the last 100 probe votes.
- a vote is healthy when stored within 2 s of being sent; `lost` = acknowledged, then neither in Postgres nor still queued in Redis.
  Recovery = from the first unhealthy vote after the fault to the start of the final unbroken run of at least 5 healthy votes.
- phases per experiment: `baseline` (already measured once, reused) → `inject` (fault_duration_s=10) → `observe`
  → `restore` → `recover` (until 5 consecutive successful votes, max 30 s) → `settle` (3 s) → `done`.
- guard thresholds: error_rate over the last 3 s >= 0.6, or p95 latency >= 3000 ms, or unhealthy_services >= 3
  (blast radius), or experiment wall time > 45 s. Any breach: stop the fault immediately, restore, mark `guard_tripped`
  with the reason. The guard is plain code, never a model.

## Types (TypeScript notation; backend returns exactly these JSON shapes)
```ts
type RunnerKind = "docker" | "sim";
type ServiceState = "running" | "stopped" | "paused" | "unhealthy" | "restarting" | "degraded";
type Source = "rules" | "local" | "small" | "large" | "cache";           // who decided; matches the cost ledger
type Severity = "critical" | "high" | "medium" | "low";
type FailureType = "crash" | "hang" | "network_cut" | "cpu_throttle" | "traffic_spike" | "compound" | "latency"; // latency optional

interface GraphNode { id: string; role: "web" | "api" | "worker" | "cache" | "queue" | "database" | "other";
  image: string; ports: string[]; healthcheck: boolean; restart: string | null; stateful: boolean;
  replicas: number; depends_on: string[]; fan_in: number; spof: boolean; notes: string[]; }
interface GraphEdge { from: string; to: string; kind: "depends_on" | "dataflow"; label: string; }
interface Graph { nodes: GraphNode[]; edges: GraphEdge[]; }

interface Experiment { id: string; type: FailureType; target: string; title: string; description: string;
  params: Record<string, number | string>; risk_hint: number /* 0..1 from graph rules */; }

interface Prediction { experiment_id: string; summary: string; expected_error_pct: number; expected_votes_lost: number;
  expected_recovery_s: number; confidence: "low" | "medium" | "high"; reason: string; hypothesis: string;
  recommended_fault_s: number /* how safely: shorter windows for riskier experiments */; decided_by: Source; model: string; }

type VoteState = "ok" | "queued" | "error" | "lost" | "pending";       // one cell of the fleet grid
interface TimelinePoint { t: number /* s since inject start */; ok: number; err: number; p95_ms: number;
  error_rate: number; unhealthy: number; }

interface Outcome { sent: number; acked: number; stored: number; lost: number; errors: number;
  error_pct: number; p95_ms: number; peak_p95_ms: number; recovery_s: number | null /* null = never recovered */;
  availability_pct: number; guard_tripped: boolean; guard_reason: string | null; data_loss: boolean; }

interface ExperimentResult { run_id: string; experiment: Experiment; prediction: Prediction | null; outcome: Outcome;
  timeline: TimelinePoint[]; fleet: VoteState[]; started_at: number; duration_s: number; label: string /* "before" | "after fix" | free text */;
  prediction_error: { errors_pct_points: number; votes_lost: number; recovery_s: number } | null;
  notes: string[] /* e.g. a secondary failure the final safety check repaired */; }

interface Pick { experiment_id: string; reason: string; hypothesis: string; decided_by: Source; model: string; order: number; }
interface Finding { id: string; service: string; experiment_id: string; severity: Severity; title: string;
  evidence: string[]; impact: string; confidence: "low" | "medium" | "high"; }
interface Fix { id: string; finding_id: string; title: string; change_type: "compose" | "config" | "code" | "process";
  snippet: string; steps: string[]; expected_score_gain: number; auto_applicable: boolean; }
interface Score { value: number /* 0..100 */; breakdown: { name: string; points: number; max: number; note: string }[]; }
```

## Endpoints (all JSON under /api)
| Method + path | Body | Returns |
|---|---|---|
| GET `/health` | | `{ok, title, tiers, runner:{kind:RunnerKind, ready:boolean, detail:string}}` (tiers as in the kit) |
| GET `/target` | | `{name, compose_file, graph:Graph, assumptions:string[], services:ServiceStatus[], unit:{singular:string, plural:string, stored:string, lost:string}}` (`unit` labels come from the target adapter, see TARGETS.md; the UI must use them instead of hard-coding "votes") |
| POST `/target/start` | | `{ok, detail}` runs `docker compose up -d` (sim: resets the simulation) |
| POST `/target/stop` | | `{ok, detail}` |
| GET `/catalog` | | `{experiments:Experiment[]}` ordered by risk_hint desc |
| POST `/baseline` | `{duration_s?:number}` | `{rps, error_pct, p95_ms, votes_sent, votes_stored}` (also stored in state) |
| POST `/predict` | `{experiment_id}` | `Prediction` (planner agent; rules fallback) |
| POST `/run` | `{experiment_id, label?, fault_duration_s?}` | `{run_id}` starts async; poll `/state` |
| POST `/campaign/start` | `{budget:number, strategy:"ai"\|"random", seed?:number}` | `{ok}`; runs picks sequentially |
| POST `/campaign/stop` | | `{ok}` abort + restore |
| POST `/killswitch` | | `{ok, restored:string[]}` manual stop, restores every service to its original state |
| GET `/state` | | `State` below (poll every 500 ms) |
| POST `/analyze` | | `{findings:Finding[], score:Score, decided_by:Source, model:string}` risk agent over all results |
| POST `/fixes` | | `{fixes:Fix[], decided_by:Source, model:string}` fix agent over findings |
| POST `/apply-fixes` | `{fix_ids:string[]}` | `{ok, overlay:string, detail}` writes `docker-compose.fixes.yml` override and applies it (engineer approval) |
| POST `/rerun` | `{experiment_ids?:string[]}` | `{ok, experiments}` re-runs (default: the experiments behind applied fixes), labelled "after fix", then analyzes |
| POST `/twin` | | `{ok}` rehearses the whole catalog on the digital twin; results in `state.twin` and `state.compare` |
| POST `/reset` | | `{ok, detail}` removes applied fixes and clears runs (demo reset) |
| GET `/targets`, POST `/target/select` | `{name}` | list targets / switch the app under test (voting, hawkbit) |
| GET `/report` | | `{markdown:string, score:Score\|null, score_history:{label:string,value:number,ts:number}[]}` |
| GET `/report.md` | | the markdown as a download |
| GET `/architecture.md` | | architecture overview markdown (hackathon deliverable) |
| GET `/ledger`, POST `/ledger/reset` | | as in the kit |
| GET `/snapshot` | | last saved full state, for replay when something breaks on stage |

```ts
interface ServiceStatus { id: string; state: ServiceState; health: "healthy" | "unhealthy" | "none"; restarts: number; cpu_pct: number | null; mem_mb: number | null; }
interface State {
  runner: { kind: RunnerKind; ready: boolean; detail: string };
  services: ServiceStatus[];
  baseline: { rps: number; error_pct: number; p95_ms: number } | null;
  phase: "idle" | "baseline" | "inject" | "observe" | "restore" | "recover" | "settle" | "done";
  busy: boolean;                          // an experiment, re-run, campaign or twin rehearsal is in progress
  current: null | { run_id: string; experiment: Experiment; prediction: Prediction | null; label: string;
    elapsed_s: number; fault_duration_s: number; timeline: TimelinePoint[]; fleet: VoteState[];
    guard: { armed: boolean; tripped: boolean; reason: string | null;
             thresholds: { error_rate: number; p95_ms: number; unhealthy: number; max_wall_s: number }; seconds_left: number };
    counters: { sent: number; acked: number; stored: number; lost: number; errors: number } };
  results: ExperimentResult[];            // finished runs, newest last
  campaign: { status: "idle" | "running" | "done" | "stopped"; strategy: "ai" | "random"; budget: number;
    picks: Pick[]; remaining: string[] } ;
  analysis: null | { findings: Finding[]; score: Score; decided_by: Source; model: string };
  fixes: null | { fixes: Fix[]; decided_by: Source; model: string };
  applied_fixes: string[];
  score_history: { label: string; value: number; ts: number }[];   // one entry per /analyze, "before fix" / "after fix"
  twin: null | { status: "running" | "done"; rows: { experiment_id: string; title: string; target: string; type: FailureType;
    penalty: number; error_pct: number; lost: number; recovery_s: number | null; guard_tripped: boolean }[]; ran_at: number; seconds?: number };
  compare: null | { planner_runs_to_top3: number | null; random_runs_to_top3: number | null; note: string };
  cost: CostSummary;                      // as in the kit's ledger.summary()
  error: string | null;
}
```

## Agent behaviour (backend)
Three agents, each = one `llm.complete(...)` call with `min_tier="local"`, a strict JSON `accept` check, and a rules
fallback on `NoModelAvailable` (log fallbacks with `ledger.record_rule`). Keep prompts short: send a compact JSON of
the graph and measured numbers, never raw logs.
1. **Planner** (failure): input = graph summary + catalog + results so far. Output JSON `{experiment_id, reason, hypothesis, expected_error_pct, expected_votes_lost, expected_recovery_s, confidence}`. Must pick an experiment not yet run (accept check). Adaptive: it sees previous outcomes and may deprioritise or escalate (for example: "worker pause was harmless, so test redis kill while the worker is down"). The rules fallback ranks by `risk_hint` and updates by outcome.
2. **Risk**: input = results with measured outcomes. Output JSON `{findings:[...]}` with evidence copied from the numbers (accept check: every finding cites an existing experiment_id and numbers that appear in that outcome). Score is computed by code (`score.py`), the model only writes the explanation.
3. **Fix**: input = findings + graph. Output JSON `{fixes:[...]}`, with concrete compose snippets for compose-level fixes. The deterministic `auto_applicable` fixes are: `restart: unless-stopped` on a service, a healthcheck, `deploy.resources` limits. Everything else is advice.

Planner-vs-random comparison (`compare`): run the same catalog under both strategies offline against the simulation
(or from real results) and report how many runs each needs to surface the top-3 weaknesses. Compute, never type in.

Resilience score (code, deterministic, documented in `score.py` and shown in the report): start at 100 and subtract
for each experiment by severity of outcome: silent data loss (largest), no recovery, guard tripped, long recovery,
high error pct; add nothing. Weight by the service's fan_in. Clamp 0..100. After applying fixes and re-running the same
experiments, the score must be computable again so the before/after comparison is real.

## Frontend views (tabs)
1. **Live** (default, the demo): left, the dependency map with nodes coloured live by `services[].state`; click a node
   to choose a failure for it. Centre: the 100-cell fleet grid (ok green, queued amber, error red, lost black/crit hatched,
   pending grey) and a live chart of error rate and p95. Right: the prediction card (predicted vs actual, appears after the run),
   guard status with countdown, and a big Kill-switch button. Judge flow: click service → `POST /predict` → show prediction →
   "Confirm and inject" → `POST /run` → watch → predicted vs actual.
2. **Campaign**: the planner's picks in order with reasons and hypotheses, running with `strategy` toggle (AI vs random), progress, the `compare` line.
3. **Findings**: findings ranked by severity with evidence, the resilience score with breakdown, fixes with snippets, "Approve and apply" button, then a "Re-run" action that repeats the same experiments and shows before/after score.
4. **Report**: the markdown report rendered, download buttons for report and architecture overview.
5. **Cost**: the kit's CostGauge and ledger.
Header: product name, runner badge (Docker or Simulation), model tier status, replay button (loads `/snapshot` or the copy saved in localStorage).
Design: reuse the existing tokens (paper, surface, ink, graphite, rule, cobalt, crit, warn, ok); sentence case, no shadows, gradients, emoji or all-caps. Must work at 1280 px wide and on a phone; dark mode supported.
Dev without a backend: provide `VITE_MOCK=1` which serves a scripted fake of every endpoint in the browser, using the shapes above, so the UI can be built and screenshot-tested before the backend exists.
