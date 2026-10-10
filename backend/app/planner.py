"""Experiment planner: picks the next experiment with a reason, adapting to what has been measured.

The rules planner is deterministic (so AI vs random can be computed offline on the twin); a model may pick instead,
but only from the remaining catalog, and its pick is rejected otherwise.
"""
from __future__ import annotations

import json
import random
import re

from . import ledger, llm, score


def _adapt(exp: dict, results: list[dict], graph: dict) -> tuple[float, str]:
    """Priority = graph risk, then adjusted by observed behaviour. Returns (priority, reason)."""
    node = next((n for n in graph["nodes"] if n["id"] == exp["target"]), None)
    prio = exp["risk_hint"]
    why = []
    if node:
        bits = []
        if node["spof"]:
            bits.append(f"single point of failure for {node['fan_in']} services")
        if node["restart"] is None:
            bits.append("no restart policy")
        if node["stateful"]:
            bits.append("holds state")
        if bits:
            why.append(f"{exp['target']}: {', '.join(bits)}")
    done = {r["experiment"]["id"]: r for r in results}
    # escalate: a harmless pause/hang of a consumer suggests units pile up somewhere; test whether they survive
    if exp["type"] == "compound":
        steps = str(exp["params"].get("steps", ""))
        for r in done.values():
            o = r["outcome"]
            if r["experiment"]["type"] in ("hang", "crash") and r["experiment"]["target"] in steps and o["errors"] == 0:
                prio += 0.3
                why.append(f"{r['experiment']['title']} looked harmless ({o['errors']} errors), so test whether the "
                           "queued work survives a second failure")
                break
    # diversify: a target that already showed its weakness is less informative
    hurt = [r for r in done.values() if r["experiment"]["target"] == exp["target"] and score.penalty(r, 0)[0] > 10]
    if hurt:
        prio -= 0.3 * len(hurt)
        why.append(f"{exp['target']} already showed a weakness in {hurt[-1]['experiment']['id']}")
    return prio, "; ".join(why) or "covers a service not tested yet"


def rules_pick(remaining: list[dict], results: list[dict], graph: dict) -> tuple[dict, str]:
    ranked = sorted(((*_adapt(e, results, graph), e) for e in remaining), key=lambda x: (-x[0], x[2]["id"]))
    prio, why, exp = ranked[0]
    return exp, why


async def pick(remaining: list[dict], results: list[dict], graph: dict) -> dict:
    exp, why = rules_pick(remaining, results, graph)
    ids = {e["id"] for e in remaining}
    prompt = json.dumps({
        "remaining": [{"id": e["id"], "type": e["type"], "target": e["target"], "risk": e["risk_hint"]} for e in remaining],
        "graph": [{k: n[k] for k in ("id", "role", "restart", "fan_in", "spof", "stateful")} for n in graph["nodes"]],
        "measured": [{"id": r["experiment"]["id"], "errors_pct": r["outcome"]["error_pct"], "lost": r["outcome"]["lost"],
                      "recovery_s": r["outcome"]["recovery_s"], "guard": r["outcome"]["guard_tripped"]} for r in results[-8:]],
    }, separators=(",", ":"))
    system = ("You plan chaos experiments. Pick the next experiment that is most likely to reveal a new, serious "
              "weakness, using the dependency graph and what was measured. Reply JSON only: "
              '{"experiment_id":"...","reason":"one sentence","hypothesis":"one sentence"}')

    allowed = set(re.findall(r"\d+(?:\.\d+)?", prompt))

    def accept(text: str) -> bool:
        d = llm.extract_json(text) or {}
        said = str(d.get("reason", "")) + " " + str(d.get("hypothesis", ""))
        # no invented numbers: everything it quotes must be in the graph or the measurements it was given
        return d.get("experiment_id") in ids and bool(str(d.get("reason", "")).strip()) and \
            set(re.findall(r"\d+(?:\.\d+)?", said)) <= allowed

    try:
        res = await llm.complete("plan", prompt, system=system, max_tokens=200, min_tier="small", accept=accept)
        d = llm.extract_json(res.text) or {}
        chosen = next(e for e in remaining if e["id"] == d["experiment_id"])
        return {"experiment": chosen, "reason": str(d["reason"]).strip(), "hypothesis": str(d.get("hypothesis", "")).strip(),
                "decided_by": res.source, "model": res.model}
    except llm.NoModelAvailable:
        ledger.record_rule(task="plan", tokens_in=ledger.estimate_tokens(system + prompt), tokens_out=40)
        return {"experiment": exp, "reason": why, "hypothesis": exp["description"], "decided_by": "rules",
                "model": "rules engine"}


def random_order(catalog: list[dict], seed: int) -> list[dict]:
    order = list(catalog)
    random.Random(seed).shuffle(order)
    return order


def runs_to_top(order_ids: list[str], top: set[str]) -> int | None:
    seen: set[str] = set()
    for i, eid in enumerate(order_ids, 1):
        if eid in top:
            seen.add(eid)
            if seen == top:
                return i
    return None


def compare(storm: list[dict], catalog: list[dict], graph: dict, trials: int = 200) -> dict:
    """How many runs each strategy needs to surface the 3 worst weaknesses, using the twin's measured outcomes."""
    by_id = {r["experiment"]["id"]: r for r in storm}
    fan = {n["id"]: n["fan_in"] for n in graph["nodes"]}
    pen = {eid: score.penalty(r, fan.get(r["experiment"]["target"], 0))[0] for eid, r in by_id.items()}
    top = {eid for eid, _ in sorted(pen.items(), key=lambda x: (-x[1], x[0]))[:3] if pen[eid] > 0}
    if not top:
        return {"planner_runs_to_top3": None, "random_runs_to_top3": None, "note": "The twin found no harmful experiment."}
    # the planner adapts as it goes, seeing each twin outcome before the next pick
    remaining, seen, order = [e for e in catalog if e["id"] in by_id], [], []
    while remaining:
        exp, _ = rules_pick(remaining, seen, graph)
        order.append(exp["id"])
        seen.append(by_id[exp["id"]])
        remaining = [e for e in remaining if e["id"] != exp["id"]]
    ai = runs_to_top(order, top)
    exps = [by_id[i]["experiment"] for i in by_id]
    rand = [runs_to_top([e["id"] for e in random_order(exps, s)], top) for s in range(trials)]
    rand_avg = round(sum(r for r in rand if r) / max(1, sum(1 for r in rand if r)), 1)
    return {"planner_runs_to_top3": ai, "random_runs_to_top3": rand_avg,
            "note": f"Top 3 weaknesses on the twin: {', '.join(sorted(top))}. Planner order found them in {ai} runs; "
                    f"random order needs {rand_avg} on average over {trials} shuffles (of {len(by_id)} experiments)."}
