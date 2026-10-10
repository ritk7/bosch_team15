# Chaos Sentinel

**An AI that predicts what a failure will do, breaks your app safely behind a kill switch that is plain code, proves
every unit of work that silently disappeared, recommends the fix, and re-runs to prove the fix worked.**

Bosch AI hackathon 2026 · Team No-15 "Lil bosch": Ritwik Katta, Nandan Murali Nari, Akram Lateef, Tejas, Akhilesh K.

Targets: Docker's open-source [example-voting-app](https://github.com/dockersamples/example-voting-app) (5 services) and
Bosch's [Eclipse hawkBit](https://github.com/eclipse-hawkbit/hawkbit) over-the-air update server (monolith + MySQL + RabbitMQ).

## What it does

    map ──► rehearse on a digital twin ──► plan (AI, with reasons) ──► predict ──► inject behind the guard
         ──► measure every unit by id ──► restore ──► explain (ranked findings) ──► fix (engineer approves) ──► re-run

| Bosch asks | Where it is |
|---|---|
| Map services, dependencies, data flows | Live tab map: compose file plus the data flows it does not declare; fan-in, single points of failure, restart policies |
| Baseline monitoring | Measured before the first experiment: rate, error %, p95, units stored |
| Failures with safety limits | Crash (true SIGKILL), hang, network cut, CPU throttle (overloaded database), traffic spike, compound failures. The guard trips on error rate ≥ 60% over 3 s, p95 ≥ 3 s, ≥ 3 services down, or > 45 s, then restores everything |
| Intelligent planning | AI campaign planner with a reason per pick, adapting to measurements; digital twin rehearsal of the whole catalog in seconds; AI vs random computed (voting app: 3 runs vs 11.9 to find the top 3 weaknesses) |
| Errors, slowdowns, recovery | Every run: error %, p95 and peak p95, recovery time, units lost silently, timeline and a 100-unit grid |
| Findings, fixes, score | Ranked findings with measured evidence (the model may reword, never invent numbers), fixes with compose snippets, deterministic resilience score with breakdown |
| Stretch goals | Live dashboard; combined failures (ghost votes); apply a fix and re-run: on real Docker the score went **57 → 74** after `restart: unless-stopped` |
| Observability tool | Prometheus + Grafana overlay scraping our `/metrics`; real CPU and memory per container on the map |
| Low cost | Rules first, small model second (Claude Haiku via an OpenAI-compatible router), response cache, cost ledger. Works with no model at all |

**Ghost votes** (verified on real Docker): pause the worker, then crash Redis. 224 votes got HTTP 200; 24 never reached
the database, and the worker then died on reconnect. An HTTP health check calls this healthy. We count lost votes by id.

## Run it

Needs Python 3.12+, Node 22.18+ (frontend tests use native TypeScript support). Docker Desktop for the real targets.

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

## The demo (90 seconds)
1. Live tab: click **redis** → **Pause worker, then crash redis**. The AI prediction appears; optionally type your own guess.
2. **Confirm and inject**: the worker freezes, Redis dies, units pile up in red on the map, the grid fills amber.
3. The guard trips and restores; the reveal shows predicted vs measured, and your guess against the AI. Votes lost silently.
4. Findings tab: **Analyse runs** → ranked weaknesses and score → tick the restart fix → **Approve and apply** → **Re-run**: score rises.
5. Campaign tab: **Rehearse all on the twin** (heatmap in seconds), AI planner against random.
6. Header: switch the target to **Bosch hawkBit update server**: same tool, Bosch's own OTA server; units become devices.

## Layout
`backend/app`: `mapper.py` (graph), `probe.py` (units by id), `guard.py` (kill switch), `injectors.py` (failures),
`campaign.py` (engine), `planner.py`, `analysis.py`, `score.py`, `fixes.py`, `report.py`, `cli.py`, `runner.py` and
`docker_runner.py` (sim and Docker), `targets/` (voting, hawkBit adapters and twins). `frontend/src`: Live, Campaign,
Findings, Report, Cost views. Docs: [architecture](docs/CONTRACT.md), [timeline](docs/TIMELINE.md), [demo](docs/DEMO.md).

## Verified, and not
- Verified on real Docker (voting app, M2 Mac, Docker 29.8): selftest, crash, ghost votes, guard trips and restores,
  fix and re-run 57 → 74, restore after the tool itself restarts mid-fault.
- hawkBit: built from hawkBit's source and docs and tested on its twin; real-server verification is
  `python dev.py selftest --target hawkbit` on a 16 GB+ machine.
- Not built: network latency/packet loss injection (needs a privileged netem helper), disk-full, Kubernetes.
