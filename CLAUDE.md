# Chaos Sentinel: instructions for Claude Code

Bosch AI hackathon, 36 hours, team of five. Read this file fully. Open the others only when the task needs them:
- `docs/CONTRACT.md`: exact API routes and JSON shapes. Backend and frontend must match it field for field.
- `docs/TARGETS.md`: the apps we test (voting app by default, hawkBit optional), their ground truth, the experiment catalog.
- `docs/DEMO.md`: the judge demo script, pitches, hard questions, checklists. Read it before polishing anything.

Skeleton state: the model router, cost ledger, response cache, cost gauge and design tokens work and are tested.
`backend/app/main.py` and `frontend/src/App.tsx` are stubs. Everything in section 6 is still to be built.

## 1. The competition and what is judged
Bosch's problem: "Build an AI system that intelligently introduces controlled failures into a cloud-native application
environment and analyzes how the system responds to improve its resilience." Chaos engineering done by hand is slow and
unfocused; the AI must decide what to break, when and how safely, then explain what the failure revealed and how to fix it.
Their five steps: (1) pick a free, open-source, containerised sample app, map services, dependencies and data flows (automatic
or semi-automatic); (2) plan experiments intelligently (riskiest or most informative first, using the dependency map and observed
behaviour); (3) inject failures with safety limits (auto-stop on error rate, latency or blast radius); (4) observe errors,
slowdowns, recovery time (an open-source observability tool should watch the app); (5) analyse and report: weaknesses, fixes,
ideally a ranked resilience score. Allowed failures: container crash, slow or lossy network, full disk, overloaded database,
failed dependency, traffic spike. Out of scope: real or production systems, anything outside our own sandbox, anything that
touches other teams. Recommending fixes is enough; auto-repair is optional. Any open-source or hosted model is allowed and
judges want to see AI used. Bosch repeatedly stressed LOW COST and high ROI (it is moving off expensive rented AI servers).
- Day 1 minimum: running app, dependency map, one safe failure injection with a working kill switch, short demo.
- Day 2 minimum: 3+ failure types run, AI-written ranked findings report with fixes, demo video of at most 3 minutes,
  architecture overview, findings report, all five deliverables submitted 30 minutes early (confirm the list with organisers).
- Real schedule: judges check Saturday 13:00; top 10 teams are shortlisted at 18:00 and build on Sunday; final Sunday; interviews Monday.

## 2. What we build and why it wins
Most teams will build "an LLM reads logs after someone kills a container". We build a closed loop that proves itself:

    map  ->  predict  ->  inject (behind a deterministic kill switch)  ->  measure  ->  restore  ->  explain  ->  fix  ->  re-run

Seven properties, each is a scoring story. Do not drop any of them to add something else:
1. **Predict before inject.** The planner states a hypothesis and numbers (errors %, units lost, recovery s). After the run the UI shows
   predicted vs actual. Being wrong is fine; the planner then updates its next pick. This is the memorable demo moment.
2. **Business-level correctness, not HTTP 200.** We count units of work that were acknowledged but never stored (silent data loss).
3. **Adaptive planner with reasons**, compared against random order. The numbers (runs to find the top 3 weaknesses) are computed.
4. **Safety is code, not a model.** Kill switch thresholds, label-scoped containers, restore in `finally`, restore after a crash.
5. **Closed loop.** Approve a fix, apply it (generated compose override), re-run the same experiments, the resilience score rises.
6. **Cheap.** Rules first, smallest model second, cache, ledger proves the cost. It demos with no API key at all.
7. **Fits a Bosch workflow.** Works on any compose file and ships a CI gate (`python -m app.cli gate --min-score 80` exits non-zero),
   so "catch weaknesses before production" is literally a pipeline step.

Judge-facing demo in one breath: a judge clicks any service on the dependency map, the AI says what it expects to happen, the
judge confirms, the container dies, a grid of 100 probe units turns red, the kill switch restores the service, and predicted vs
actual is revealed. Full script in `docs/DEMO.md`.

## 3. Vocabulary (use these names in code and UI)
- **Target**: the app under test (a compose project) plus its adapter (`docs/TARGETS.md`). **Unit**: one probe item (a vote, a device update).
- **Experiment**: one failure type on one service (`crash`, `hang`, `network_cut`, `cpu_throttle`, `traffic_spike`, compound ones).
- **Fault window**: seconds the fault is active. **Guard**: the deterministic kill switch. **Outcome**: measured numbers for one run.
- **Prediction**: the planner's expected outcome. **Finding**: a weakness with evidence. **Fix**: a recommended change. **Score**: 0..100.

## 4. Constraints that decide the design
- Laptop: MacBook M2, 8 GB RAM. Default target = Docker's example-voting-app (vote, redis, worker, db, result), about 500 MB, built from
  source for arm64: `git clone https://github.com/dockersamples/example-voting-app targets/voting`, then `docker compose up -d --build`
  inside it (first build 3-5 min, needs internet). Docker Desktop memory limit 3-4 GB. No Kubernetes. hawkBit needs a 16 GB machine.
- No local LLM on that laptop. Agents call `llm.complete(min_tier="small")` (Claude Haiku via `ANTHROPIC_API_KEY` in `backend/.env`) and
  fall back to deterministic rules when no model answers. A teammate with more RAM may enable the local tier (Ollama).
- The product must work end to end in three modes: `RUNNER=sim` (in-memory simulation of the voting app, labelled "Simulation" in the UI,
  used for building, tests and as a stage fallback), `RUNNER=docker` (real Docker CLI), `RUNNER=auto` (docker if the daemon answers).
- Observability: Bosch wants an open-source observability tool. Ship `docker-compose.observability.yml` (cAdvisor + Prometheus + Grafana)
  as an optional overlay and read from Prometheus when reachable. Our own probe numbers remain the source of truth for outcomes.

## 5. Architecture (files to create under backend/app unless noted)
- `runner.py`: `Runner` protocol (list services with state/health/restarts, kill, start, pause, unpause, network disconnect/connect,
  cpu limit, exec, `restore_all`). `DockerRunner` shells out to the `docker compose` CLI (no Docker SDK dependency). `SimRunner` is the simulation.
- `backend/app/targets/` (a Python package, not the repo-root `targets/` folder that holds cloned apps such as `targets/voting`): one adapter per target
  (`voting.py`, `hawkbit.py`, `generic.py`): roles, extra dataflow edges, workload, business check, extra experiments.
- `mapper.py`: compose YAML to the dependency graph (nodes, edges, fan_in, spof, stateful, healthcheck, restart, replicas).
- `probe.py`: sends units at 10/s, classifies each (ok, queued, error, lost, pending), keeps the last-100 fleet grid, timeline and counters.
- `injectors.py`: experiment catalog and the fault and restore for each type. `guard.py`: thresholds, 0.5 s check loop, trip and restore.
- `campaign.py` and `state.py`: phases, live State object, one experiment at a time, snapshot to `.cache/last_state.json`, `/api/killswitch`.
- `agents.py`: planner (with `predict`), risk, fix. `score.py`: deterministic resilience score. `fixes.py`: compose override writer and applier.
- `report.py`: findings report and architecture overview as markdown. `cli.py`: `gate`. `main.py`: routes from the contract.
- Frontend (`frontend/src`): `views/Live`, `Campaign`, `Findings`, `Report`, `Cost`; components for the map, fleet grid, live chart, guard card,
  prediction card; `lib/api.ts` typed client mirroring the contract; `lib/mock.ts` served when `VITE_MOCK=1` so the UI can be built and tested offline.
- Agents: each is one `llm.complete(...)` call with a strict JSON `accept` check, compact JSON in (graph summary, measured numbers, never raw logs),
  and a rules fallback on `NoModelAvailable` logged with `ledger.record_rule`. Accept checks must reject invented evidence: every finding must
  cite an experiment id that exists and numbers that appear in that outcome. The rules fallbacks must be good enough to demo alone.
- Score: deterministic, documented in `score.py`, shown with a breakdown. Requirements: it falls when any experiment loses data or never
  recovers; it does NOT rise when more harmless experiments are added (blend the worst case with the mean); fan-in of the target service
  weights it; it is reproducible, so the same results always give the same number and a re-run after a fix gives a real before/after.

## 6. Build order, with "done when" checks (run `python dev.py check` after each step)
1. **Milestone 1, Day-1 minimum.** `runner.py` (Sim first, then Docker), `mapper.py`, `probe.py`, `guard.py`, the crash experiment, `/api/state`, Live view
   with the map and a Kill button. Done when, with `RUNNER=sim python dev.py`: the UI shows the 5-service map; choosing "crash redis" turns the
   grid red, the guard trips and restores, and the result shows error %, units lost and recovery seconds. Unit tests prove the guard trips and that every
   experiment restores the original service states.
2. **Docker path.** `python dev.py selftest` (start `targets/voting`, run one real crash on redis, print PASS/FAIL per step: app up, map built,
   baseline, fault injected, guard restored, recovery measured). Unit-test the exact docker command lines with a fake executor.
3. **Prediction and reveal.** `/api/predict`, fleet grid, predicted-vs-actual reveal. Add hang (pause), network_cut, cpu_throttle, traffic_spike and
   the compound "pause worker then kill redis" (must produce acknowledged-but-lost units in sim).
4. **Agents, score, fix, re-run.** risk and fix agents, `score.py`, `/api/analyze`, `/api/fixes`, `/api/apply-fixes`, Findings view. Done when:
   after applying "restart: unless-stopped" and re-running the same experiment the score is higher and the crash experiment recovers by itself.
5. **Campaign and report.** AI vs random strategy, runs-to-top-3 computed, `report.py`, Report view, `cli.py gate`, optional observability overlay.
6. **Polish.** Replay (`/api/snapshot` plus localStorage), README with exact deploy steps, backup video script, cost view, hawkBit adapter if time allows.

## 7. Rules
1. Every model call goes through `await llm.complete(task, prompt, min_tier=..., accept=...)`; never call a provider SDK directly. Log rule decisions
   with `ledger.record_rule`. Keep prompts compact. Say in the UI which tier or rules decided each agent output.
2. Safety is code, never a model: act only on containers of the target compose project (label `com.docker.compose.project`), allow-list service names
   from the compose file, never touch the host network or other containers, restore in `finally`, persist the active fault to
   `backend/.cache/active_fault.json` and restore on startup. Never run a fault without the guard armed.
3. Every number a judge sees is computed from measurements. No typed-in figures. Put assumptions in the report.
4. UI: tokens in `frontend/src/index.css` (paper, surface, ink, graphite, rule, cobalt, crit, warn, ok); sentence case; no shadows, gradients, emoji or
   all-caps labels; colour is never the only signal (hatch or mark for lost units); keyboard operable; responsive 390 to 1440 px; dark mode.
5. No new dependencies unless needed (pyyaml expected). Keep `frontend/src/lib/api.ts` in sync with `docs/CONTRACT.md` in the same change.
6. Say plainly what was not verified. The Docker path can only be verified on a machine with Docker; do not claim it works until selftest passes.
7. Do not build: Kubernetes support, auth, chat features, autonomous repair that bypasses approval, anything that touches systems outside the sandbox.

## 8. Known pitfalls
- `docker compose ps --format json` prints NDJSON on some versions and a JSON array on others; parse both.
- Docker Desktop on Mac: `tc netem` needs a privileged helper image, so latency injection is optional; `network disconnect`, `pause`, `kill` and `update --cpus`
  work everywhere. The vote service listens on host port 8080 and hawkBit's management API also uses 8080, so run one target at a time.
- The voting app has no `restart:` policy, so a killed container stays dead until the kill switch or fix restarts it. That is a finding, not a bug in our tool.
- Probe votes need unique `voter_id` cookies or Postgres upserts collapse them. Verify by ids, never by a count alone.
- The worker is a .NET build: allow several minutes the first time and keep Docker's memory at 3-4 GB.

## 9. How to reply (saves tokens)
Make the change, then reply in at most 5 lines: what changed, which files, how to see it. Don't paste code unless asked. One change per message.
If a request is ambiguous, take the simplest reading and say which in one line. After a milestone, run `/compact`.

## 10. Commands
- `pip install -r backend/requirements.txt`; `python dev.py` (API :8000, UI :5173); `python dev.py serve` (one port, demo mode);
  `python dev.py check` (tests, type check, build); `python dev.py selftest` (real Docker, to be written).
- While building: `RUNNER=sim python dev.py`. For the real thing: start Docker Desktop, then `RUNNER=docker python dev.py serve`.

## First prompt to paste
"Read CLAUDE.md and docs/CONTRACT.md. Plan Milestone 1 in five bullets and wait for my OK. Then build it with RUNNER=sim first and keep
`python dev.py check` green."
