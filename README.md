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
Target app for the demo: `git clone https://github.com/dockersamples/example-voting-app targets/voting`

## What works (Milestone 1, simulation)
Dependency map from the compose file plus undeclared data flows; probe that checks every vote by id in the database; crash
experiment on any service; deterministic guard (error rate, p95, blast radius, wall time) that stops the fault and restores
every service; kill switch; restore of an interrupted fault on startup; prediction before each run (rules, or a model when
configured) and predicted-vs-measured reveal; Live view and Cost view. The simulation follows the voting app's source code.

Not built yet: the Docker runner (Milestone 2), more failure types, findings, score, fixes, campaign, report.
