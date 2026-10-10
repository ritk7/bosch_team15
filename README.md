# Chaos Sentinel

An AI that stress-tests a Docker Compose app by breaking it on purpose, safely, then explains what it found and how to fix it.
Built for the Bosch AI hackathon. Start with `CLAUDE.md`; the exact API is in `docs/CONTRACT.md`.

## Start (skeleton)
```
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
python dev.py          # API :8000, UI :5173
python dev.py check    # backend tests, type check, UI build
```
Target app for the demo: `git clone https://github.com/dockersamples/example-voting-app targets/voting`

## What already works
Model router (cheapest model that passes a check), cost ledger and cache, cost gauge, design tokens. The chaos engine
(runner, mapper, probe, guard, agents, score, report) and the five views are to be built following `CLAUDE.md`.
