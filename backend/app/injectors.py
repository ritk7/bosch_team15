"""Experiment catalog and the fault for each type. Restoring is always runner.restore_all (back to recorded states)."""
from __future__ import annotations

TITLES = {"crash": "Crash {t}"}
DESCRIPTIONS = {
    "crash": "Kill the {t} process. It stays down for the fault window unless a restart policy brings it back.",
}


def risk_hint(node: dict, max_fan_in: int, on_request_path: bool) -> float:
    """Graph rules, 0..1: shared, stateful, unprotected services on the request path are the riskiest to lose."""
    score = 0.1
    score += 0.25 if node["spof"] else 0.0
    score += 0.2 * (node["fan_in"] / max_fan_in) if max_fan_in else 0.0
    score += 0.15 if node["stateful"] else 0.0
    score += 0.1 if node["restart"] is None else 0.0
    score += 0.2 if on_request_path else 0.0
    return round(min(1.0, score), 2)


def catalog(graph: dict, sync: set[str]) -> list[dict]:
    max_fan = max((n["fan_in"] for n in graph["nodes"]), default=0)
    out = []
    for node in graph["nodes"]:
        t = node["id"]
        out.append({"id": f"crash-{t}", "type": "crash", "target": t, "title": TITLES["crash"].format(t=t),
                    "description": DESCRIPTIONS["crash"].format(t=t), "params": {},
                    "risk_hint": risk_hint(node, max_fan, t in sync)})
    return sorted(out, key=lambda e: (-e["risk_hint"], e["id"]))


async def apply(runner, exp: dict) -> None:
    if exp["type"] == "crash":
        await runner.crash(exp["target"])
    else:
        raise ValueError(f"Failure type '{exp['type']}' is not built yet")
