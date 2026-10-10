"""Experiment catalog and the fault for each type. Restoring containers is always runner.restore_all (back to recorded
states); faults that change something else (probe rate, a scheduled second step) return cleanups that the engine runs."""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

Cleanup = Callable[[], Awaitable[None]]

# How much a failure type usually hurts, relative to a crash. Used only to rank the catalog before anything is measured.
TYPE_WEIGHT = {"compound": 1.1, "crash": 1.0, "network_cut": 0.9, "hang": 0.9, "cpu_throttle": 0.6, "traffic_spike": 0.5}

TEXT = {
    "crash": ("Crash {t}", "Kill the {t} process. It stays down for the fault window unless a restart policy brings it back."),
    "hang": ("Hang {t}", "Freeze {t} (docker pause): the process keeps its connections open but stops answering."),
    "network_cut": ("Cut {t} off the network", "Disconnect {t} from the project's networks; it keeps running and keeps its memory."),
    "cpu_throttle": ("Throttle {t} to {cpus} CPU", "Limit {t} to {cpus} of a CPU (docker update --cpus), like an overloaded host."),
    "traffic_spike": ("Traffic spike on {t} ({multiplier}×)", "Send {multiplier} times the normal probe rate to {t} for the fault window."),
}


def risk_hint(node: dict, max_fan_in: int, on_request_path: bool, kind: str = "crash", entry: bool = False) -> float:
    """Graph rules, 0..1: shared, stateful, unprotected services on the request path are the riskiest to lose.
    The entry service counts as a single point of failure too: every user depends on it, even if no service does."""
    score = 0.1
    score += 0.25 if node["spof"] or (entry and node["replicas"] == 1) else 0.0
    score += 0.2 * (node["fan_in"] / max_fan_in) if max_fan_in else 0.0
    score += 0.15 if node["stateful"] else 0.0
    score += 0.1 if node["restart"] is None else 0.0
    score += 0.2 if on_request_path else 0.0
    return round(min(1.0, score * TYPE_WEIGHT.get(kind, 1.0)), 2)


def _exp(kind: str, node: dict, max_fan: int, sync: set[str], entry: str | None = None, **params) -> dict:
    t = node["id"]
    title, desc = TEXT[kind]
    return {"id": f"{kind.replace('_', '-')}-{t}", "type": kind, "target": t,
            "title": title.format(t=t, **params), "description": desc.format(t=t, **params), "params": params,
            "risk_hint": risk_hint(node, max_fan, t in sync, kind, entry=t == entry)}


def catalog(graph: dict, sync: set[str], entry: str | None = None, extra: list[dict] | None = None) -> list[dict]:
    nodes = graph["nodes"]
    max_fan = max((n["fan_in"] for n in nodes), default=0)
    out = []
    for node in nodes:
        out.append(_exp("crash", node, max_fan, sync, entry))
        if node["role"] in ("cache", "queue", "database", "worker") or node["id"] in sync:
            out.append(_exp("hang", node, max_fan, sync, entry))
        if node["stateful"] and node["fan_in"] > 0:
            out.append(_exp("network_cut", node, max_fan, sync, entry))
        if node["role"] in ("database", "worker"):
            out.append(_exp("cpu_throttle", node, max_fan, sync, entry, cpus=0.1))
        if node["id"] == entry:
            out.append(_exp("traffic_spike", node, max_fan, sync, entry, multiplier=6))
    out += extra or []
    return sorted(out, key=lambda e: (-e["risk_hint"], e["id"]))


async def _step(runner, action: str, svc: str) -> None:
    if action == "crash":
        await runner.crash(svc)
    elif action == "pause":
        await runner.pause(svc)
    elif action == "disconnect":
        await runner.disconnect(svc)
    else:
        raise ValueError(f"Unknown step '{action}'")


async def apply(runner, exp: dict, probe=None) -> list[Cleanup]:
    """Inject the fault. Returns cleanups for anything restore_all does not cover."""
    kind, t, p = exp["type"], exp["target"], exp.get("params", {})
    if kind == "crash":
        await runner.crash(t)
    elif kind == "hang":
        await runner.pause(t)
    elif kind == "network_cut":
        await runner.disconnect(t)
    elif kind == "cpu_throttle":
        await runner.set_cpus(t, float(p.get("cpus", 0.1)))
    elif kind == "traffic_spike":
        if probe is None:
            raise ValueError("traffic_spike needs the probe")
        normal = probe.rate
        probe.rate = normal * float(p.get("multiplier", 6))

        async def calm() -> None:
            probe.rate = normal

        return [calm]
    elif kind == "compound":
        steps = [s.split(":") for s in str(p["steps"]).split(",")]  # "pause:worker,crash:redis"
        await _step(runner, *steps[0])

        async def later() -> None:
            for action, svc in steps[1:]:
                await asyncio.sleep(float(p.get("delay_s", 4)) / runner.clock.speed)
                await _step(runner, action, svc)

        task = asyncio.create_task(later())
        if probe is not None:
            probe.monitor(task)

        async def cancel() -> None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        return [cancel]
    else:
        raise ValueError(f"Failure type '{kind}' is not built")
    return []
