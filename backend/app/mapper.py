"""Compose YAML to the dependency graph (contract types GraphNode, GraphEdge, Graph).

depends_on edges come from the compose file; dataflow edges the file cannot express come from the target adapter.
An optional fixes overlay (docker-compose.fixes.yml) is merged on top, so applied fixes show up in the map.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROLE_BY_IMAGE = (("redis", "cache"), ("rabbitmq", "queue"), ("kafka", "queue"), ("postgres", "database"),
                 ("mysql", "database"), ("mariadb", "database"), ("mongo", "database"))
STATEFUL_ROLES = {"cache", "queue", "database"}


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _depends(spec: dict) -> list[str]:
    dep = spec.get("depends_on") or []
    return sorted(dep) if isinstance(dep, (list, dict)) else []


def load_services(compose: Path, overlay: Path | None = None) -> dict[str, dict]:
    services = {k: v or {} for k, v in (_load(compose).get("services") or {}).items()}
    services = {k: v for k, v in services.items() if not v.get("profiles")}  # profile-only services are not started
    if overlay and overlay.exists():
        for name, extra in (_load(overlay).get("services") or {}).items():
            if name in services:
                services[name] = {**services[name], **(extra or {})}
    return services


def build_graph(compose: Path, *, roles: dict[str, str] | None = None, extra_edges: list[dict] | None = None,
                notes: dict[str, list[str]] | None = None, overlay: Path | None = None) -> dict:
    roles, notes = roles or {}, notes or {}
    services = load_services(compose, overlay)
    edges = [{"from": s, "to": d, "kind": "depends_on", "label": "starts after"}
             for s, spec in services.items() for d in _depends(spec) if d in services]
    edges += [e for e in (extra_edges or []) if e["from"] in services and e["to"] in services]

    nodes = []
    for name, spec in services.items():
        build = spec.get("build")
        context = build.get("context", ".") if isinstance(build, dict) else build
        image = spec.get("image") or f"built from {context or '.'}"
        role = roles.get(name) or next((r for key, r in ROLE_BY_IMAGE if key in str(image)), None) \
            or ("web" if spec.get("ports") else "other")
        restart = spec.get("restart") or ((spec.get("deploy") or {}).get("restart_policy") or {}).get("condition")
        restart = None if restart in (None, "no", "none", False) else str(restart)
        replicas = int(((spec.get("deploy") or {}).get("replicas")) or 1)
        named_volume = any(isinstance(v, str) and not v.startswith((".", "/", "~")) and ":" in v
                           for v in spec.get("volumes") or [])
        callers = {e["from"] for e in edges if e["to"] == name}
        n_notes = list(notes.get(name, []))
        if restart is None:
            n_notes.append("No restart policy: if the process exits, the container stays down.")
        if replicas == 1 and callers:
            n_notes.append(f"Single replica used by {', '.join(sorted(callers))}.")
        if role in ("cache", "queue") and not named_volume and any(k in str(image) for k in ("redis", "memcached")):
            n_notes.append("Keeps its data in memory only (no persistent volume).")
        nodes.append({
            "id": name, "role": role, "image": str(image),
            "ports": [str(p) for p in spec.get("ports") or []],
            "healthcheck": bool(spec.get("healthcheck")), "restart": restart,
            "stateful": role in STATEFUL_ROLES or named_volume, "replicas": replicas,
            "depends_on": _depends(spec), "fan_in": len(callers), "spof": replicas == 1 and bool(callers),
            "notes": n_notes,
        })
    return {"nodes": nodes, "edges": edges}


def topo_order(graph: dict) -> list[str]:
    """Dependencies first (callee before caller), stable by node order. Used to restore services safely."""
    ids = [n["id"] for n in graph["nodes"]]
    needs = {i: {e["to"] for e in graph["edges"] if e["from"] == i and e["to"] != i} for i in ids}
    order: list[str] = []
    while len(order) < len(ids):
        ready = [i for i in ids if i not in order and needs[i] <= set(order)]
        order += ready or [next(i for i in ids if i not in order)]  # break a cycle rather than loop forever
    return order


def sync_path(graph: dict, entry: str) -> set[str]:
    """The entry service plus everything it calls while answering a request (dataflow edges from the entry)."""
    return {entry} | {e["to"] for e in graph["edges"] if e["from"] == entry and e["kind"] == "dataflow"}
