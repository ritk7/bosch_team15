# Chaos Sentinel

[![tests](https://github.com/ritk7/bosch_team15/actions/workflows/tests.yml/badge.svg)](https://github.com/ritk7/bosch_team15/actions/workflows/tests.yml) ![license: MIT](https://img.shields.io/badge/license-MIT-blue)

**An AI that predicts what a failure will do, breaks your app safely behind a kill switch that is plain code, proves
every unit of work that silently disappeared, recommends the fix, and re-runs to prove the fix worked.**

Bosch AI hackathon 2026 · Team No-15 "Lil bosch": Ritwik Katta, Nandan Murali Nair, Akram Lateef, Tejas, Akhilesh K.

**Main target: Bosch's [Eclipse hawkBit](https://github.com/eclipse-hawkbit/hawkbit)**, the over-the-air software update server
for devices and vehicles (monolith + MySQL + RabbitMQ). Units of work are devices receiving an update: "updated" or "stuck".
Blueprint: Docker's [example-voting-app](https://github.com/dockersamples/example-voting-app), where the method was built
and proven on an 8 GB laptop before pointing the same tool at hawkBit.

## At a glance

| | |
|---|---|
| **Problem** | Chaos engineering by hand is slow, unfocused and risky; health checks say "healthy" while work is silently lost |
| **What we built** | A closed loop: map → rehearse on a digital twin → AI plans and predicts → inject behind a plain-code kill switch → check every unit by id → restore → AI explains and recommends a fix → engineer approves → re-run proves it |
| **3 AI agents** | Planner (ranks experiments with reasons, predicts the damage), Risk (ranked findings), Fix (concrete changes). Claude Haiku 4.5 + rules fallback; numbers a model invents are rejected |
| **Safety** | The guard is code, never a model: error rate ≥ 60%, p95 ≥ max(3 s, 10× baseline), blast radius, 45 s cap; restore in `finally`, undone after a crash, scoped to one compose project |
| **Real Bosch hawkBit** | 15 real runs, 10 failure types, 867 devices through its real APIs: 0 updates lost; crashing the hawkBit server → 94.3% of updates fail, not back within 30 s; 500 ms DB delay → updates 13× slower ([report](docs/sample-findings-report-hawkbit.md), [raw evidence](docs/evidence/hawkbit-2026-10-11)) |
| **Fix proven by re-run** | Voting-app blueprint on real Docker: resilience score **29 → 56.5** after an approved `restart: unless-stopped` |
| **Tested** | 135 backend tests + 33 end-to-end system checks on real Docker + hawkBit selftest 6/6 PASS |
| **Verify it yourself** | [**JUDGING.md**](JUDGING.md): every claim, the command that proves it, and what you should see |
| **Docs** | [Architecture overview (one page)](docs/ARCHITECTURE_OVERVIEW.md) · [Architecture and workflow](docs/ARCHITECTURE.md) · [API contract](docs/CONTRACT.md) · [timeline](docs/TIMELINE.md) · [team form](docs/TEAM_FORM.md) |

## What it does

    map ──► rehearse on a digital twin ──► plan (AI, with reasons) ──► predict ──► inject behind the guard
         ──► measure every unit by id ──► restore ──► explain (ranked findings) ──► fix (engineer approves) ──► re-run

| Bosch asks | Where it is |
|---|---|
| Map services, dependencies, data flows | Live tab map: compose file plus the data flows it does not declare; fan-in, single points of failure, restart policies; **manual corrections** in the UI |
| Baseline monitoring | Measured before the first experiment: rate, error %, p95, units stored |
| Failures with safety limits | Crash (true SIGKILL), hang, network cut, **network delay and packet loss** (tc netem), CPU throttle, **database overload** (query flood + CPU limit), traffic spike, combined failures (incl. traffic spike while a service is down). The guard trips on error rate ≥ 60% over 3 s, p95 ≥ max(3 s, 10× baseline), collateral services down (blast radius), or > 45 s, then restores everything |
| Intelligent planning | AI ranked plan (every experiment, a reason each); AI campaign planner adapting to measurements and the twin; fault window chosen by risk, injection only into a steady app; digital twin rehearsal of the whole catalog in seconds; AI vs random computed (voting app: 3 runs vs 11.9 to find the top 3 weaknesses) |
| Errors, slowdowns, recovery | Every run: error %, p95 and peak p95, recovery time, units lost silently, timeline and a 100-unit grid |
| Findings, fixes, score | Ranked findings with measured evidence (the model may reword, never invent numbers), fixes with compose snippets, deterministic resilience score with breakdown and **services ranked by resilience** |
| Stretch goals | Live dashboard; combined failures (ghost votes); apply a fix and re-run: on real Docker (voting blueprint) the score went **29 → 56.5** after `restart: unless-stopped` |
| Observability tool | Prometheus + Grafana overlay scraping our `/metrics`; real CPU and memory per container on the map |
| Low cost | Rules first, small model second (Claude Haiku via an OpenAI-compatible router), response cache, cost ledger. Works with no model at all |

**Findings on Bosch's real hawkBit** (11 Oct, Tejas's 24 GB M4 Pro: 15 real runs, 10 experiment types, 867 devices driven
through hawkBit's real management and device APIs; report [`docs/sample-findings-report-hawkbit.md`](docs/sample-findings-report-hawkbit.md),
raw evidence [`docs/evidence/hawkbit-2026-10-11`](docs/evidence/hawkbit-2026-10-11)). Resilience score **30/100**.
- **Updates are durable:** 0 devices stuck in any run.
- **Critical, hawkBit itself is the weak point:** crash the hawkBit server and 94.3% of device updates fail; it was not
  back within the 30 s recovery window despite `restart: always` (one replica, slow Java start). Weakest service: hawkbit 44.7/100.
- **Slow, frozen or cut MySQL stalls updates:** device-update p95 rises from 0.56 s to 4–8 s (+500 ms delay: 7.3 s, peak 8.0 s)
  and the guard tripped in all three. RabbitMQ failures had no measurable impact (100/100).
- **Intermittent wedge:** on 10 Oct the API stayed dead after a MySQL disruption in 2 of 4 runs while containers said
  "running"; not reproduced in the 11 Oct runs. Chaos Sentinel checks that the app answers, not just that containers run.
- Recommended: fail fast on MySQL timeouts (bounded connection and read waits, 503 + Retry-After; written by the fix agent),
  database connection validation, and a health check that exercises the database.

Blueprint check on the voting app (real Docker): pause the worker, then crash Redis: 225 votes got HTTP 200, 23 never
reached the database.

## Run it

Needs Python 3.12+, Node 22.18+ (frontend tests use native TypeScript support). Docker Desktop for the real targets.
Fastest: `./setup.sh voting` (or `./setup.sh hawkbit`) does everything below and runs the checks.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
RUNNER=sim python dev.py           # digital twin, no Docker needed. Open http://localhost:5173
```

Real voting app (8 GB laptop is fine, Docker memory 3-4 GB):

```bash
git clone https://github.com/dockersamples/example-voting-app targets/voting
(cd targets/voting && docker compose up -d --build)     # first build 3-5 min
python dev.py selftest                                    # one real crash, PASS/FAIL per step
RUNNER=auto python dev.py                                 # Docker when the target runs, otherwise the twin
```

Real Bosch hawkBit (16 GB+ machine): see [docs/HAWKBIT.md](docs/HAWKBIT.md).

Optional models: copy `backend/.env.example` to `backend/.env` and set `SMALL_PROVIDER=openai`, `SMALL_BASE_URL`,
`SMALL_MODEL`, `SMALL_API_KEY` (any OpenAI-compatible router), or `ANTHROPIC_API_KEY`. `.env` is git-ignored.

CI gate (fails the pipeline below a resilience score):

```bash
cd backend && python -m app.cli gate --min-score 80 --budget 5 [--target hawkbit]
```

Observability (open-source, optional): Prometheus scrapes our live measurements from `/metrics` (units by state, error
rate, p95, guard, score, per-container CPU and memory from `docker stats`) and Grafana shows them:

```bash
docker compose -p observability -f docker-compose.observability.yml up -d   # Grafana: http://localhost:3000/d/chaos-sentinel
```

Checks: `python dev.py check` (backend tests, frontend chart regression tests, type check, UI build).

Charts show errors on a fixed 0–100% scale and latency separately in seconds with an automatic range. A 100% rolling
3-second error window does not mean every request in the entire run failed. Completed runs record the measured
restoration time; older saved runs lack this timestamp and are not given invented fault shading. "Recovery not observed"
means the probe did not establish five consecutive healthy units during the bounded recovery window, not proof that
the application can never recover. Baselines are remeasured after restarts, fixes and resets.
The cost ledger is persisted atomically in the git-ignored `backend/.cache/cost_ledger.json`; resetting the ledger is explicit.
Restoration waits up to 90 seconds per service for running/healthy status: Docker's default health-check interval is
30 seconds, so a recovered MySQL process can remain `starting` until its next check. This is verification time, not
permission to keep injecting a fault after a guard trips.
Once restoration is verified and the guard is disarmed, recovery has its own bounded 30-second observation window;
time spent waiting for service health checks does not subtract from that window. Self-tests use unique device IDs
on every invocation and append to saved run history.

## The demo (90 seconds, hawkBit first)
1. **Blueprint, 15 s:** voting app on real Docker, click **redis** → **Crash redis**: the guard trips and restores. Proof the tool works.
2. Header: switch the target to **Bosch hawkBit update server**: 3 services mapped; units become devices getting an update.
3. Click **mysql** → **Slow network on mysql (+500 ms)**. The AI prediction appears; optionally type your own guess.
4. **Confirm and inject**: watch the devices (updated or stuck), the guard watches and restores; the reveal shows predicted vs measured.
   On the real server device-update latency went from 0.56 s to about 8 s and the guard stopped it; no device was lost.
   The worst real result: crashing hawkBit itself, 94.3% of updates failed and it was not back within 30 s.
5. Pipeline tab: the 12 stages (mapper, twin, planner, prediction, injector, guard, probe, restore, risk, score, fix, approve)
   as a live wiring diagram; each stage shows its real latest output, the running stage pulses, the wire is cut when the guard trips.
6. Findings tab: **Analyse runs** → ranked weaknesses and score → tick a fix → **Approve and apply** → **Re-run**.
7. Campaign tab: **Rehearse all on the twin** (heatmap in seconds), **Rank every experiment** (AI plan with reasons).
   Press **S** (or Stage mode) for a projector.

## The UI
Tabs: **Live** (status strip with the run's phases and counters, dependency map, prediction and reveal, guard, live chart,
last 100 units, experiment library, run history), **Pipeline** (the system as a live wiring diagram), **Campaign**, **Findings**
(ranked weaknesses, score, coverage of untested services, fixes), **Report**, **Cost**. Dark and light mode; works from 390 px up.

## Layout
`backend/app`: `mapper.py` (graph), `probe.py` (units by id), `guard.py` (kill switch), `injectors.py` (failures),
`campaign.py` (engine), `planner.py`, `analysis.py`, `score.py`, `fixes.py`, `report.py`, `cli.py`, `runner.py` and
`docker_runner.py` (sim and Docker), `targets/` (voting, hawkBit adapters and twins). `frontend/src`: Live, Campaign,
Findings, Report, Cost views. Docs: [architecture and workflow](docs/ARCHITECTURE.md), [API contract](docs/CONTRACT.md), [timeline](docs/TIMELINE.md), [demo](docs/DEMO.md).

## Safety notes (stated up front)
- Two short-lived helper containers act on **one container of our own project at a time**: `alpine` with `--pid=host`
  sends SIGKILL to exactly that container's process (so restart policies still fire, unlike `docker kill`), and
  `netshoot` with `NET_ADMIN` joins only that container's network namespace to add or remove a netem rule.
- **Full disk is deliberately not injected**: on Docker Desktop every container shares one VM disk, so filling it would
  affect everything on the machine, which the brief rules out.

## Verified, and not
- Verified on real Docker (voting app, M2 Mac, Docker 29.8): selftest, crash, hang, ghost votes, network delay and
  packet loss, DB overload, CPU throttle, traffic spike, spike while the worker is down; guard trips and restores;
  fix and re-run 29 → 56.5; restore after the tool itself restarts mid-fault. Real runs found and fixed our own bugs
  (reused voter cookie, `docker update --cpus 0` silently keeping a CPU limit).
- **Real Bosch hawkBit** (Tejas's M4 Pro): 11 Oct final validation on the latest build: selftest 6/6 PASS and 15 real
  runs across 10 experiment types (crash, hang, network cut, +500 ms latency, traffic spike, RabbitMQ cut + MySQL crash
  on hawkbit, mysql and rabbitmq); 867 devices through hawkBit's real APIs, 0 stuck; every run restored. Findings above.
  On 10 Oct an earlier build saw hawkBit's API stay dead after a MySQL disruption in 2 of 4 runs (containers running)
  until restarted; Chaos Sentinel now checks the application itself and restarts and reports a wedged service.
- On hawkBit the risk findings in the final session came from the rules fallback (the AI answer failed the number check),
  and its only fix is code advice, so the before/after re-run is proven on the voting blueprint.
- Not built: disk-full (safety, see above), Kubernetes.
