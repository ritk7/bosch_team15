"""Agents. Each is one llm.complete call with a strict accept check and a rules fallback that is good enough alone.

Milestone 1 has the planner's predict step; pick, risk and fix arrive with later milestones.
"""
from __future__ import annotations

import json

from . import ledger, llm
from .guard import Thresholds
from .probe import WINDOW_S

RESTART_S = 2.0  # rule of thumb: seconds for a container to start and answer
CONFIDENCE = ("low", "medium", "high")
SYSTEM = ("You predict the outcome of one chaos experiment on a containerised app. Reply with JSON only: "
          '{"expected_error_pct":0-100,"expected_votes_lost":int,"expected_recovery_s":number,'
          '"confidence":"low|medium|high","reason":"one sentence","hypothesis":"one sentence"}')


def _compact(graph: dict) -> dict:
    return {"services": [{k: n[k] for k in ("id", "role", "restart", "stateful", "fan_in", "spof", "healthcheck")}
                         for n in graph["nodes"]],
            "edges": [f"{e['from']}->{e['to']} ({e['label']})" for e in graph["edges"] if e["kind"] == "dataflow"]}


def rules_prediction(exp: dict, graph: dict, *, entry: str, sync: set[str], rate: float, fault_s: float,
                     history: list[dict], unit_plural: str) -> dict:
    """Deterministic hypothesis from the graph. Formulas are rough on purpose; the reveal shows how far off they were."""
    target = exp["target"]
    node = next(n for n in graph["nodes"] if n["id"] == target)
    prev = [r for r in history if r["experiment"]["id"] == exp["id"]]
    if prev:  # we measured this one before: predict what we saw
        o = prev[-1]["outcome"]
        rec = o["recovery_s"] if o["recovery_s"] is not None else 30.0
        return {"expected_error_pct": round(o["error_pct"]), "expected_votes_lost": o["lost"],
                "expected_recovery_s": round(rec, 1), "confidence": "high",
                "reason": f"the last run of {exp['id']} measured these numbers and nothing in the graph changed since",
                "hypothesis": f"{target} behaves as it did in run {prev[-1]['run_id']}"}

    self_heal = node["restart"] in ("always", "unless-stopped", "on-failure")
    consumers = {e["from"] for e in graph["edges"] if e["kind"] == "dataflow" and e["to"] in sync and e["from"] not in sync}
    downstream = {e["to"] for e in graph["edges"] if e["kind"] == "dataflow" and e["from"] in consumers} - sync
    th = Thresholds()
    trip_s = WINDOW_S * th.error_rate + 0.5  # all requests failing cross the guard's error limit after this long

    if target in sync:
        fault_phase = fault_s if self_heal else min(fault_s, trip_s)
        outage = RESTART_S if self_heal else fault_phase + RESTART_S
        window = 1.0 + fault_phase + RESTART_S + 5 / rate + 1.0
        err = min(100.0, 100 * outage / window)
        lost = round(rate * 0.2) if node["stateful"] and any("memory only" in s for s in node["notes"]) else 0
        why = (f"{entry} calls {target} on every request" if target != entry else f"{target} is the entry point")
        why += "; it restarts by itself" if self_heal else \
            ", it has no restart policy, so it stays down until the guard restores it"
        hyp = f"{entry} fails fast while {target} is down" + \
              (f"; {unit_plural} waiting in {target}'s memory are lost" if lost else "")
        return {"expected_error_pct": round(err), "expected_votes_lost": lost, "expected_recovery_s": round(outage, 1),
                "confidence": "medium", "reason": why, "hypothesis": hyp}
    if target in consumers or target in downstream:
        rec = (RESTART_S if self_heal else fault_s + RESTART_S) + fault_s  # backlog drains about as fast as it built up
        why = f"{target} is off the request path, so {entry} keeps answering while {unit_plural} queue up"
        return {"expected_error_pct": 0, "expected_votes_lost": 0, "expected_recovery_s": round(rec, 1),
                "confidence": "low" if target in downstream else "medium", "reason": why,
                "hypothesis": f"no errors; {unit_plural} are delayed, not lost, and drain after {target} is back"}
    return {"expected_error_pct": 0, "expected_votes_lost": 0, "expected_recovery_s": 0.0, "confidence": "medium",
            "reason": f"nothing on the path of {entry} depends on {target}",
            "hypothesis": f"probe {unit_plural} are unaffected; only {target}'s own users notice"}


def _accept(text: str) -> bool:
    d = llm.extract_json(text)
    try:
        return (d is not None and 0 <= float(d["expected_error_pct"]) <= 100 and int(d["expected_votes_lost"]) >= 0
                and 0 <= float(d["expected_recovery_s"]) <= 300 and d["confidence"] in CONFIDENCE
                and bool(str(d["reason"]).strip()) and bool(str(d["hypothesis"]).strip()))
    except (KeyError, TypeError, ValueError):
        return False


def _summary(p: dict, unit_plural: str, unit_singular: str) -> str:
    lost = p["expected_votes_lost"]
    return (f"I expect about {p['expected_error_pct']}% errors, {lost} {unit_singular if lost == 1 else unit_plural} lost, "
            f"recovery {p['expected_recovery_s']} s, confidence {p['confidence']}, because {p['reason']}.")


async def predict(exp: dict, graph: dict, *, entry: str, sync: set[str], rate: float, fault_s: float,
                  history: list[dict], unit_plural: str, unit_singular: str, baseline: dict | None) -> dict:
    rules = rules_prediction(exp, graph, entry=entry, sync=sync, rate=rate, fault_s=fault_s, history=history,
                             unit_plural=unit_plural)
    past = [{"id": r["experiment"]["id"], "error_pct": r["outcome"]["error_pct"], "lost": r["outcome"]["lost"],
             "recovery_s": r["outcome"]["recovery_s"]} for r in history[-6:]]
    prompt = json.dumps({"experiment": {k: exp[k] for k in ("id", "type", "target", "description")},
                         "graph": _compact(graph), "entry": entry, "probe_rate_per_s": rate, "fault_s": fault_s,
                         "guard": "stops the fault when error rate over 3 s >= 60% or p95 >= 3 s",
                         "baseline": baseline, "past_runs": past}, separators=(",", ":"))
    try:
        res = await llm.complete("predict", prompt, system=SYSTEM, max_tokens=300, min_tier="small", accept=_accept)
        d = llm.extract_json(res.text) or {}
        p = {"expected_error_pct": round(float(d["expected_error_pct"])), "expected_votes_lost": int(d["expected_votes_lost"]),
             "expected_recovery_s": round(float(d["expected_recovery_s"]), 1), "confidence": d["confidence"],
             "reason": str(d["reason"]).strip().rstrip("."), "hypothesis": str(d["hypothesis"]).strip()}
        source, model = res.source, res.model
    except llm.NoModelAvailable:
        ledger.record_rule(task="predict", tokens_in=ledger.estimate_tokens(SYSTEM + prompt),
                           tokens_out=ledger.estimate_tokens(json.dumps(rules)))
        p, source, model = rules, "rules", "rules engine"
    return {"experiment_id": exp["id"], "summary": _summary(p, unit_plural, unit_singular), **p, "decided_by": source, "model": model}
