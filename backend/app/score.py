"""Resilience score, 0..100. Deterministic: the same results always give the same number.

Per experiment (latest run of each experiment id counts), a penalty from the measured outcome:
  silent data loss      20 + up to 10 more as lost units grow (50 lost = full)
  never recovered       25  (no unbroken run of 5 healthy units within 30 s of the restore)
  guard tripped         12  (the app needed the kill switch)
  secondary failure     10  (a service failed after the fault was removed)
  slow recovery          0..8 (recovery from 10 s to 30 s)
  errors                 0..15 (error percentage of all units)
The sum is weighted by how many services depend on the target: x (1 + 0.15 x fan_in), capped at 60.
Total = worst penalty in full + 35% of all the others, so the worst weakness dominates and adding harmless experiments
can never raise the score. Score = 100 - total, clamped to 0..100.
"""
from __future__ import annotations

CAP = 60.0
OTHERS = 0.35


def latest(results: list[dict]) -> list[dict]:
    """Latest run per experiment id, in first-run order."""
    by_id: dict[str, dict] = {}
    for r in results:
        by_id[r["experiment"]["id"]] = r
    return list(by_id.values())


def penalty(result: dict, fan_in: int) -> tuple[float, list[str]]:
    o = result["outcome"]
    parts: list[tuple[float, str]] = []
    if o["lost"] > 0:
        parts.append((20 + 10 * min(1.0, o["lost"] / 50), f"{o['lost']} acknowledged unit{'' if o['lost'] == 1 else 's'} lost"))
    if o["recovery_s"] is None:
        parts.append((25.0, "never recovered within 30 s"))
    elif o["recovery_s"] > 10:
        parts.append((8 * min(1.0, (o["recovery_s"] - 10) / 20), f"slow recovery {o['recovery_s']} s"))
    if o["guard_tripped"]:
        parts.append((12.0, "needed the kill switch"))
    if result.get("notes"):
        parts.append((10.0, "secondary failure after restore"))
    if o["error_pct"] > 0:
        parts.append((15 * o["error_pct"] / 100, f"{o['error_pct']}% errors"))
    raw = sum(p for p, _ in parts)
    return min(CAP, raw * (1 + 0.15 * fan_in)), [n for _, n in parts]


def compute(results: list[dict], graph: dict) -> dict:
    fan = {n["id"]: n["fan_in"] for n in graph["nodes"]}
    rows = []
    for r in latest(results):
        p, notes = penalty(r, fan.get(r["experiment"]["target"], 0))
        rows.append((p, r, notes))
    rows.sort(key=lambda x: (-x[0], x[1]["experiment"]["id"]))
    breakdown, total = [], 0.0
    for i, (p, r, notes) in enumerate(rows):
        counted = p if i == 0 else p * OTHERS
        total += counted
        if p > 0:
            breakdown.append({"name": r["experiment"]["title"], "points": -round(counted, 1), "max": CAP,
                              "note": ("worst case, counted in full: " if i == 0 else "counted at 35%: ") + ", ".join(notes)})
    harmless = sum(1 for p, _, _ in rows if p == 0)
    if harmless:
        breakdown.append({"name": f"{harmless} experiments without measurable harm", "points": 0, "max": 0,
                          "note": "they do not raise the score"})
    return {"value": round(max(0.0, min(100.0, 100 - total)), 1), "breakdown": breakdown,
            "services": per_service(results, graph)}


def per_service(results: list[dict], graph: dict) -> list[dict]:
    """Ranked resilience per service (weakest first): the same formula, restricted to experiments on that service."""
    fan = {n["id"]: n["fan_in"] for n in graph["nodes"]}
    out = []
    for n in graph["nodes"]:
        rows = sorted(((penalty(r, fan.get(n["id"], 0))[0], r) for r in latest(results)
                       if r["experiment"]["target"] == n["id"]), key=lambda x: -x[0])
        if not rows:
            out.append({"service": n["id"], "value": None, "experiments": 0, "worst": None})
            continue
        total = rows[0][0] + OTHERS * sum(p for p, _ in rows[1:])
        out.append({"service": n["id"], "value": round(max(0.0, 100 - total), 1), "experiments": len(rows),
                    "worst": rows[0][1]["experiment"]["title"] if rows[0][0] > 0 else None})
    return sorted(out, key=lambda x: (x["value"] is None, x["value"] if x["value"] is not None else 0, x["service"]))
