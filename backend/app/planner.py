"""Experiment planner: picks the next experiment with a reason, adapting to what has been measured.

The rules planner is deterministic (so AI vs random can be computed offline on the twin); a model may pick instead,
but only from the remaining catalog, and its pick is rejected otherwise.
"""
from __future__ import annotations

import asyncio
import json
import random
import re

from . import ledger, llm, score


NUM = r"\d+(?:\.\d+)?"


def numbers_ok(said: str, source: str) -> bool:
    """Every number the model writes must come from its input (rounding allowed): no invented figures."""
    allowed = [float(x) for x in re.findall(NUM, source)]
    for x in re.findall(NUM, said):
        v = float(x)
        if not any(abs(v - a) <= max(0.5, 0.05 * abs(a)) or abs(v - a * 100) <= 0.5 for a in allowed):
            return False
    return True


def _adapt(exp: dict, results: list[dict], graph: dict, twin: dict[str, float] | None = None) -> tuple[float, str]:
    """Priority = graph risk, adjusted by observed behaviour and, when available, the twin's rehearsal.
    Returns (priority, reason)."""
    node = next((n for n in graph["nodes"] if n["id"] == exp["target"]), None)
    prio = exp["risk_hint"]
    why = []
    if twin:
        top = max(twin.values()) or 1.0
        predicted = twin.get(exp["id"], 0.0)
        prio = 0.4 * prio + 0.6 * (predicted / top)
        if predicted > 0:
            why.append(f"the twin rehearsal predicts penalty {predicted:g}")
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


def rules_rank(remaining: list[dict], results: list[dict], graph: dict,
               twin: dict[str, float] | None = None) -> list[tuple[float, str, dict]]:
    return sorted(((*_adapt(e, results, graph, twin), e) for e in remaining), key=lambda x: (-x[0], x[2]["id"]))


def rules_pick(remaining: list[dict], results: list[dict], graph: dict,
               twin: dict[str, float] | None = None) -> tuple[dict, str]:
    prio, why, exp = rules_rank(remaining, results, graph, twin)[0]
    return exp, why


async def rank_all(catalog: list[dict], results: list[dict], graph: dict, twin: dict[str, float] | None = None) -> dict:
    """The AI's full plan: every experiment ranked, each with a short reason, before anything runs."""
    ranked = rules_rank(catalog, results, graph, twin)
    base = [{"experiment_id": e["id"], "rank": i + 1, "priority": round(p, 2), "reason": why}
            for i, (p, why, e) in enumerate(ranked)]
    prompt = json.dumps({
        "experiments": [{"id": e["id"], "type": e["type"], "target": e["target"], "risk": e["risk_hint"],
                         "twin_penalty": (twin or {}).get(e["id"])} for e in catalog],
        "graph": [{k: n[k] for k in ("id", "role", "restart", "fan_in", "spof", "stateful")} for n in graph["nodes"]],
        "measured": [{"id": r["experiment"]["id"], "errors_pct": r["outcome"]["error_pct"], "lost": r["outcome"]["lost"],
                      "recovery_s": r["outcome"]["recovery_s"]} for r in results[-8:]],
    }, separators=(",", ":"))
    system = ("You plan chaos experiments. Rank ALL experiments from most to least informative or risky, using the "
              "dependency graph, the twin's predicted penalty and what was measured. One short reason each. "
              'Reply JSON only: {"plan":[{"experiment_id":"...","reason":"..."}]}')
    ids = {e["id"] for e in catalog}

    def accept(text: str) -> bool:
        rows = [r for r in (llm.extract_json(text) or {}).get("plan") or [] if isinstance(r, dict)]
        got = [r.get("experiment_id") for r in rows]
        if len(set(got)) != len(got) or not set(got) <= ids or len(got) < 0.7 * len(ids):
            return False
        return all(str(r.get("reason", "")).strip() for r in rows) and \
            numbers_ok(" ".join(str(r.get("reason", "")) for r in rows), prompt)

    prio = {row["experiment_id"]: row["priority"] for row in base}
    try:
        res = await asyncio.wait_for(llm.complete("rank", prompt, system=system, max_tokens=1400, min_tier="small",
                                                  accept=accept), 40)
        rows = [r for r in (llm.extract_json(res.text) or {})["plan"] if isinstance(r, dict)]
        seen = {r["experiment_id"] for r in rows}
        rows += [{"experiment_id": b["experiment_id"], "reason": b["reason"]} for b in base if b["experiment_id"] not in seen]
        plan = [{"experiment_id": r["experiment_id"], "rank": i + 1, "priority": prio[r["experiment_id"]],
                 "reason": str(r["reason"]).strip()} for i, r in enumerate(rows)]
        return {"plan": plan, "decided_by": res.source, "model": res.model}
    except (llm.NoModelAvailable, asyncio.TimeoutError):
        ledger.record_rule(task="rank", tokens_in=ledger.estimate_tokens(system + prompt), tokens_out=len(base) * 20)
        return {"plan": base, "decided_by": "rules", "model": "rules engine"}


async def pick(remaining: list[dict], results: list[dict], graph: dict, twin: dict[str, float] | None = None) -> dict:
    exp, why = rules_pick(remaining, results, graph, twin)
    ids = {e["id"] for e in remaining}
    prompt = json.dumps({
        "remaining": [{"id": e["id"], "type": e["type"], "target": e["target"], "risk": e["risk_hint"]} for e in remaining],
        "graph": [{k: n[k] for k in ("id", "role", "restart", "fan_in", "spof", "stateful")} for n in graph["nodes"]],
        "measured": [{"id": r["experiment"]["id"], "errors_pct": r["outcome"]["error_pct"], "lost": r["outcome"]["lost"],
                      "recovery_s": r["outcome"]["recovery_s"], "guard": r["outcome"]["guard_tripped"]} for r in results[-8:]],
    }, separators=(",", ":"))
    system = ("You plan chaos experiments. Pick the next experiment that is most likely to reveal a new, serious "
              "weakness, using the dependency graph and what was measured. Prefer services not tested yet. Reply JSON only: "
              '{"experiment_id":"...","reason":"one sentence","hypothesis":"one sentence"}')

    # coverage guardrail: a service already tested twice is off limits while other services are still untested
    tested: dict[str, int] = {}
    for r in results:
        tested[r["experiment"]["target"]] = tested.get(r["experiment"]["target"], 0) + 1
    untested = {e["target"] for e in remaining} - set(tested)
    saturated = {t for t, n in tested.items() if n >= 2} if untested else set()
    by_id = {e["id"]: e for e in remaining}

    def accept(text: str) -> bool:
        d = llm.extract_json(text) or {}
        said = str(d.get("reason", "")) + " " + str(d.get("hypothesis", ""))
        eid = d.get("experiment_id")
        return eid in ids and by_id[eid]["target"] not in saturated and bool(str(d.get("reason", "")).strip()) \
            and numbers_ok(said, prompt)

    try:
        res = await asyncio.wait_for(llm.complete("plan", prompt, system=system, max_tokens=200, min_tier="small",
                                                  accept=accept), 15)
        d = llm.extract_json(res.text) or {}
        chosen = next(e for e in remaining if e["id"] == d["experiment_id"])
        return {"experiment": chosen, "reason": str(d["reason"]).strip(), "hypothesis": str(d.get("hypothesis", "")).strip(),
                "decided_by": res.source, "model": res.model}
    except (llm.NoModelAvailable, asyncio.TimeoutError):
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
