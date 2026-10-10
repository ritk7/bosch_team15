"""Compose YAML to the dependency graph (contract types GraphNode, GraphEdge, Graph).

depends_on edges come from the compose file; dataflow edges the file cannot express come from the target adapter.
An optional fixes overlay (docker-compose.fixes.yml) is merged on top, so applied fixes show up in the map.
"""
from __future__ import annotations

import re
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


def _settings(spec: dict) -> list[tuple[str, str]]:
    """(key, value) pairs from environment (list or map form) and command/entrypoint."""
    env = spec.get("environment") or {}
    pairs = list(env.items()) if isinstance(env, dict) else [tuple(str(x).split("=", 1)) if "=" in str(x) else (str(x), "")
                                                           for x in env]
    pairs = [(str(k), str(v)) for k, v in pairs]
    for k in ("command", "entrypoint"):
        if spec.get(k):
            pairs.append(("", str(spec[k])))
    return pairs


def build_graph(compose: Path, *, roles: dict[str, str] | None = None, extra_edges: list[dict] | None = None,
                notes: dict[str, list[str]] | None = None, overlay: Path | None = None) -> dict:
    roles, notes = roles or {}, notes or {}
    services = load_services(compose, overlay)
    edges = [{"from": s, "to": d, "kind": "depends_on", "label": "starts after"}
             for s, spec in services.items() for d in _depends(spec) if d in services]
    # data flows the compose file reveals: a host-like setting (host/url/address key, //name, name:port, @name)
    # naming another service. A database merely *named* like a service (MYSQL_DATABASE=hawkbit) does not count.
    for src, spec in services.items():
        for key, value in _settings(spec):
            for dst in services:
                if dst == src:
                    continue
                word = rf"(?<![\w-]){re.escape(dst)}(?![\w-])"
                hosty = re.search(r"HOST|URL|URI|ADDR|SERVER|BROKER|ENDPOINT", key, re.I) and re.search(word, value)
                urlish = re.search(rf"(//|@){re.escape(dst)}(?![\w-])|{word}:\d+", value)
                if hosty or urlish:
                    edges.append({"from": src, "to": dst, "kind": "dataflow", "label": f"{key or 'command'} points at {dst}"})
                    break
    known = {(e["from"], e["to"], e["kind"]) for e in edges}
    for e in extra_edges or []:  # adapter knowledge from the app's source code (e.g. a hostname set in code)
        if e["from"] in services and e["to"] in services:
            derived = next((x for x in edges if x["from"] == e["from"] and x["to"] == e["to"] and x["kind"] == "dataflow"), None)
            if derived and e["kind"] == "dataflow":  # confirmed by the compose file: keep both explanations
                derived["label"] = f"{e['label']} ({derived['label']})"
                continue
            edges.append({**e, "label": e["label"] if "source" in e.get("label", "") else f"{e['label']} (from the app's source code)"})

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


def corrections_path(target_dir: Path) -> Path:
    return target_dir / "chaos-map.json"


def load_corrections(target_dir: Path) -> dict:
    import json

    try:
        data = json.loads(corrections_path(target_dir).read_text(encoding="utf-8"))
        return {"add": list(data.get("add", [])), "remove": [tuple(x) for x in data.get("remove", [])]}
    except (OSError, ValueError):
        return {"add": [], "remove": []}


def save_corrections(target_dir: Path, data: dict) -> None:
    import json

    corrections_path(target_dir).write_text(json.dumps({"add": data["add"], "remove": [list(x) for x in data["remove"]]},
                                                       indent=1), encoding="utf-8")


def corrected_edges(target_dir: Path, base: list[dict]) -> list[dict]:
    """Manual corrections (engineer-edited data flows) on top of the adapter's edges."""
    c = load_corrections(target_dir)
    gone = set(c["remove"])
    edges = [e for e in base if (e["from"], e["to"]) not in gone]
    known = {(e["from"], e["to"]) for e in edges}
    edges += [{**e, "kind": "dataflow", "label": e.get("label") or "added by hand"} for e in c["add"]
              if (e["from"], e["to"]) not in known and (e["from"], e["to"]) not in gone]
    return edges
