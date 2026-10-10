# Chaos Sentinel

An AI that stress-tests a Docker Compose app by breaking it on purpose, safely, then explains what it found and how to fix it.
Built for the Bosch AI hackathon. Start with `CLAUDE.md`; the exact API is in `docs/CONTRACT.md`.

## Start
```
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
RUNNER=sim python dev.py   # simulation, no Docker needed. API :8000, UI :5173
python dev.py check        # backend tests, type check, UI build
```
Open http://localhost:5173, click a service on the map, choose a failure, read the prediction, press "Confirm and inject".

### Real Docker (the voting app)
```
git clone https://github.com/dockersamples/example-voting-app targets/voting
(cd targets/voting && docker compose up -d --build)   # first build 3-5 min; Docker Desktop memory 3-4 GB
python dev.py selftest                                  # one real crash on redis, PASS/FAIL per step
RUNNER=docker python dev.py                             # or RUNNER=auto: Docker when it answers, else simulation
```

### Models (optional; everything works on rules without them)
Copy `backend/.env.example` to `backend/.env`. Any OpenAI-compatible router works for the small tier:
`SMALL_PROVIDER=openai`, `SMALL_BASE_URL`, `SMALL_MODEL`, `SMALL_API_KEY`, `SMALL_USD_IN`, `SMALL_USD_OUT`. `.env` is git-ignored.
Target app for the demo: `git clone https://github.com/dockersamples/example-voting-app targets/voting`

## What works (Milestone 1, verified on Docker and in simulation)
Dependency map from the compose file plus undeclared data flows; probe that checks every vote by id in the database; crash
experiment on any service; deterministic guard (error rate, p95, blast radius, wall time) that stops the fault and restores
every service; kill switch; restore of an interrupted fault on startup; prediction before each run (rules, or a model when
configured) and predicted-vs-measured reveal; Live view and Cost view. The simulation follows the voting app's source code.

`python dev.py selftest` passes on the real voting app (Docker 29.8, M2 Mac): baseline 25/25 votes stored, redis
killed, guard tripped on p95 latency (votes hang while Redis is gone), every service restored, recovery measured.

Not built yet: more failure types, findings, score, fixes, campaign, report.
