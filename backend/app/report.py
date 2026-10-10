"""Findings report and architecture overview as markdown (hackathon deliverables), generated from real state only."""
from __future__ import annotations

import time

from . import config


def _rec(v: float | None) -> str:
    return "not observed (bounded recovery window)" if v is None else f"{v} s"


def findings_report(engine) -> str:
    t, u = engine.target, engine.target.unit
    a, fx = engine.analysis, engine.fixes
    lines = [f"# Chaos Sentinel findings report: {t.title}", "",
             f"Generated {time.strftime('%Y-%m-%d %H:%M')} · runner: **{'Docker (real containers)' if engine.runner.kind == 'docker' else 'Simulation (digital twin)'}**"
             f" · {len(engine.results)} experiment runs · probe {engine.rate:g} {u['plural']}/s", ""]
    if a:
        lines += ["## Resilience score", "", f"**{round(a['score']['value'])} / 100**", ""]
        if len(engine.score_history) > 1:
            lines += ["Score history: " + " → ".join(f"{h['label']} {round(h['value'])}" for h in engine.score_history), ""]
        lines += ["| Item | Points | Note |", "|---|---:|---|"]
        lines += [f"| {b['name']} | {b['points']} | {b['note']} |" for b in a["score"]["breakdown"]]
        lines += ["", "Formula (backend/app/score.py): per experiment, penalties for silent data loss, no recovery, guard trip, "
                  "secondary failure, slow recovery and errors, weighted by the target's fan-in; worst counted in full, "
                  "others at 35%.", ""]
        lines += ["## Weaknesses, ranked", "",
                  f"Written by: {a['decided_by']} ({a['model']}). Evidence is copied from measured outcomes.", ""]
        for i, f in enumerate(a["findings"], 1):
            lines += [f"### {i}. [{f['severity']}] {f['title']}", "", f"Service `{f['service']}`, experiment `{f['experiment_id']}`, "
                      f"confidence {f['confidence']}.", "", f["impact"], ""] + [f"- {e}" for e in f["evidence"]] + [""]
    else:
        lines += ["_No analysis yet: run experiments, then Analyse._", ""]
    if fx:
        lines += ["## Recommended fixes", ""]
        for x in fx["fixes"]:
            state = "applied" if x["id"] in engine.applied_fixes else ("can be applied" if x["auto_applicable"] else "advice")
            lines += [f"### {x['id']}. {x['title']} ({x['change_type']}, {state}, est. +{x['expected_score_gain']} points)", "",
                      "```yaml" if x["change_type"] in ("compose", "config") else "```", x["snippet"], "```", ""]
            lines += [f"- {s}" for s in x["steps"]] + [""]
    if engine.results:
        lines += ["## All runs", "", f"| Experiment | Label | Errors | {u['plural'].capitalize()} {u['lost']} | Recovery | Guard | Prediction off by |",
                  "|---|---|---:|---:|---|---|---|"]
        for r in engine.results:
            o, pe = r["outcome"], r.get("prediction_error")
            lines.append(f"| {r['experiment']['title']} | {r['label']} | {o['error_pct']}% | {o['lost']} | {_rec(o['recovery_s'])} | "
                         f"{'tripped: ' + (o['guard_reason'] or '') if o['guard_tripped'] else 'not needed'} | "
                         f"{'—' if not pe else str(pe['errors_pct_points']) + ' pts, ' + str(pe['votes_lost']) + ' ' + u['plural'] + ', recovery ' + (str(pe['recovery_s']) + ' s' if o['recovery_s'] is not None and pe['recovery_s'] is not None else 'unavailable')} |")
            for n in r.get("notes", []):
                lines.append(f"| ↳ {n} | | | | | | |")
        lines.append("")
    if engine.compare:
        lines += ["## AI planner against random order", "", engine.compare["note"], ""]
    if engine.twin and engine.twin.get("rows"):
        lines += ["## Digital twin rehearsal", "",
                  f"{len(engine.twin['rows'])} experiments rehearsed on the twin in {engine.twin.get('seconds', '?')} s.", "",
                  "| Experiment | Predicted penalty | Errors | Lost | Recovery |", "|---|---:|---:|---:|---|"]
        for row in sorted(engine.twin["rows"], key=lambda r: -r["penalty"]):
            lines.append(f"| {row['title']} | {row['penalty']} | {row['error_pct']}% | {row['lost']} | {_rec(row['recovery_s'])} |")
        lines.append("")
    lines += ["## Assumptions and limits", ""] + [f"- {s}" for s in t.assumptions(engine.rate)] + [
        "- Guard limits: error rate over 3 s ≥ 60%, p95 ≥ 3 s, 3 services down, or 45 s wall time.",
        "- Not tested: network latency/packet loss injection (needs a privileged netem helper on Docker Desktop), disk-full.",
        "- Model costs come from the ledger (Cost tab); rupee figures use USD_INR from backend/.env.", ""]
    return "\n".join(lines)


def architecture(engine) -> str:
    g = engine.graph
    nodes = ", ".join(f"`{n['id']}` ({n['role']})" for n in g["nodes"])
    flows = "; ".join(f"{e['from']} → {e['to']} ({e['label']})" for e in g["edges"] if e["kind"] == "dataflow")
    return f"""# Chaos Sentinel: architecture overview

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

## Current target: {engine.target.title}
Services: {nodes}. Data flows: {flows}.

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
FastAPI backend, React UI, Docker CLI runner or in-memory twin (`RUNNER=docker|sim|auto`). Probe at {engine.rate:g} units/s.
Model tiers from `backend/.env` (any OpenAI-compatible router works). Runs fully on a laptop; no data leaves the machine
except model prompts (compact JSON of graph and numbers, never logs).
"""


def write_files(engine) -> list[str]:
    out = config.REPO / "reports"
    out.mkdir(exist_ok=True)
    paths = []
    for name, text in (("findings-report.md", findings_report(engine)), ("architecture.md", architecture(engine))):
        p = out / f"{engine.target.name}-{name}"
        p.write_text(text, encoding="utf-8")
        paths.append(str(p))
    return paths
