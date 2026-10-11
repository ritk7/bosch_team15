# Chaos Sentinel

**An AI that predicts what a failure will do, breaks your app safely behind a kill switch that is plain code, proves
every unit of work that silently disappeared, recommends the fix, and re-runs to prove the fix worked.**

Bosch AI hackathon 2026 · Team No-15 "Lil bosch": Ritwik Katta, Nandan Murali Nair, Akram Lateef, Tejas, Akhilesh K.

Targets: Docker's open-source [example-voting-app](https://github.com/dockersamples/example-voting-app) (5 services) and
Bosch's [Eclipse hawkBit](https://github.com/eclipse-hawkbit/hawkbit) over-the-air update server (monolith + MySQL + RabbitMQ).

**Architecture and workflow:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (diagram, one-experiment workflow, AI agents, safety, requirement coverage).

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
| Stretch goals | Live dashboard; combined failures (ghost votes); apply a fix and re-run: on real Docker the score went **57 → 74** after `restart: unless-stopped` |
| Observability tool | Prometheus + Grafana overlay scraping our `/metrics`; real CPU and memory per container on the map |
| Low cost | Rules first, small model second (Claude Haiku via an OpenAI-compatible router), response cache, cost ledger. Works with no model at all |

**Ghost votes** (verified on real Docker): pause the worker, then crash Redis. 224 votes got HTTP 200; 24 never reached
the database, and the worker then died on reconnect. An HTTP health check calls this healthy. We count lost votes by id.

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

## The demo (90 seconds)
1. Live tab: click **redis** → **Pause worker, then crash redis**. The AI prediction appears; optionally type your own guess.
2. **Confirm and inject**: the worker freezes, Redis dies, units pile up in red on the map, the grid fills amber.
3. The guard trips and restores; the reveal shows predicted vs measured, and your guess against the AI. Votes lost silently.
   The services that were faulted stay outlined on the map after the restore.
4. Pipeline tab: the 12 stages (mapper, twin, planner, prediction, injector, guard, probe, restore, risk, score, fix, approve)
   as a live wiring diagram; each stage shows its real latest output, the running stage pulses, the wire is cut when the guard trips.
5. Findings tab: **Analyse runs** → ranked weaknesses and score → tick the restart fix → **Approve and apply** → **Re-run**: score rises.
6. Campaign tab: **Rehearse all on the twin** (heatmap in seconds), **Rank every experiment** (AI plan with reasons), AI planner against random.
   Press **S** (or Stage mode) for a projector.
7. Header: switch the target to **Bosch hawkBit update server**: same tool, Bosch's own OTA server; units become devices.

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
  fix and re-run 57 → 74; restore after the tool itself restarts mid-fault. Real runs found and fixed our own bugs
  (reused voter cookie, `docker update --cpus 0` silently keeping a CPU limit).
- **Real Bosch hawkBit** (Tejas's M4 Pro, 10–11 Oct): our client drives hawkBit's real management and device APIs
  (718 devices created, all checked ones `in_sync`); crash, +500 ms latency and the combined failure were injected
  into the real stack and every container was restored. **Finding:** after a MySQL disruption, hawkBit's API stayed
  unresponsive (containers running) in 2 of 4 runs until hawkBit was restarted, consistent with database connections
  that are not re-established. Chaos Sentinel now checks the application itself, not just containers, and restarts
  and reports a wedged service as a secondary failure.
- Not built: disk-full (safety, see above), Kubernetes.
