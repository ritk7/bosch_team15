# Chaos Sentinel: architecture overview

**What it does.** An AI that stress-tests a containerised app by breaking it on purpose, safely: it maps the app,
predicts what each failure will do, injects it behind a deterministic kill switch, measures the real impact per unit of
work, then ranks weaknesses, recommends fixes, applies the approved ones and proves them by re-running.

```
compose file ──► Mapper ──► dependency graph (services, depends_on, data flows, fan-in, SPOF, restart policy)
                                │
         Digital twin ◄─────────┤ rehearses every experiment in seconds, ranks them, AI vs random computed
                                ▼
 Planner agent (AI/rules) ─► picks next experiment + reason ─► Predict agent: errors %, units lost, recovery s
                                ▼
 Injector (docker CLI, project-scoped) ─► fault ─► target app ◄── Probe: units by id, checked in the database
                                ▲                                     │
 Guard (plain code, 0.5 s loop) ── trips on error rate / p95 / blast radius / wall time ─► restore_all (finally)
                                ▼
 Risk agent ─► ranked findings (evidence = measured numbers)   Score (deterministic formula)
 Fix agent  ─► fixes ─► engineer approves ─► compose override ─► re-run ─► score before / after
                                ▼
 Report, architecture page, CI gate (`python -m app.cli gate --min-score 80`), cost ledger
```

## Current target: Bosch hawkBit update server
Services: `hawkbit` (api), `mysql` (database), `rabbitmq` (queue). Data flows: hawkbit → mysql (stores targets, rollouts, action states (SPRING_DATASOURCE_URL points at mysql)); hawkbit → rabbitmq (device messaging (DMF) and events (SPRING_RABBITMQ_HOST points at rabbitmq)).

## How the AI makes decisions
- Three model-backed agents, each one `llm.complete` call with a strict JSON check and a rules fallback that works alone:
  **planner** (next experiment and why), **predictor** (expected errors, lost units, recovery), **risk** (ranked findings),
  **fix** (concrete steps). Cheapest tier first (small model), a response cache, and a cost ledger for every decision.
- Models only choose from an allow-listed catalog and only reword findings; any number a model writes must already be in
  the measured evidence, or the answer is rejected and the rules output is used.

## Safety controls
- The guard is plain code, never a model: error rate over 3 s ≥ 60%, p95 ≥ 3 s, ≥ 3 services down, or > 45 s.
- Every command is scoped to the target's compose project (`docker compose -p <project>`), service names are allow-listed
  from the compose file; a crash is SIGKILL of exactly that container's process.
- Restore runs in `finally`; the active fault is persisted to `.cache/active_fault.json` and undone on the next start;
  a final check after the settle period restarts anything that failed later and reports it as a secondary failure.
- Fixes are applied only after an engineer approves them, as a separate override file that can be deleted to undo.

## Runtime
FastAPI backend, React UI, Docker CLI runner or in-memory twin (`RUNNER=docker|sim|auto`). Probe at 2 units/s.
Model tiers from `backend/.env` (any OpenAI-compatible router works). Runs fully on a laptop; no data leaves the machine
except model prompts (compact JSON of graph and numbers, never logs).
