"""Risk agent (findings) and fix agent (recommendations). Rules build both from measured outcomes; a model may only
reword titles, impact and steps. Its answer is rejected if it changes ids or cites a number that is not in the evidence."""
from __future__ import annotations

import json
import re

from . import ledger, llm, score

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
SELF_HEAL = {"always", "unless-stopped", "on-failure"}


def _severity(o: dict, notes: list[str]) -> str:
    if o["lost"] > 0 or o["recovery_s"] is None:
        return "critical"
    if o["guard_tripped"] or notes:
        return "high"
    if (o["recovery_s"] or 0) > 10 or o["error_pct"] > 5:
        return "medium"
    return "low"


def rules_findings(results: list[dict], graph: dict, unit: dict, runner_kind: str) -> list[dict]:
    fan = {n["id"]: n["fan_in"] for n in graph["nodes"]}
    out = []
    for r in score.latest(results):
        exp, o, notes = r["experiment"], r["outcome"], r.get("notes", [])
        p, _ = score.penalty(r, fan.get(exp["target"], 0))
        if p <= 0:
            continue
        t, plural = exp["target"], unit["plural"]
        if o["lost"] > 0:
            title = f"{o['lost']} acknowledged {plural} {unit['lost']} during “{exp['title']}”"
            impact = (f"Clients received successful HTTP responses for their {plural}, but the ids were absent from both storage and the queue. "
                      "An HTTP health check calls this healthy; only checking ids shows the loss.")
        elif o["recovery_s"] is None:
            title = f"Recovery was not observed after “{exp['title']}”"
            impact = f"The probe did not establish five consecutive healthy {plural} within the bounded recovery window."
        elif o["guard_tripped"]:
            title = f"“{exp['title']}” breaches the safety limits"
            impact = f"The measured run reached a safety threshold and triggered restoration; overall errors were {o['error_pct']}%."
        else:
            title = f"“{exp['title']}” degrades service: {o['error_pct']}% errors, recovery {o['recovery_s']} s"
            impact = "Users notice errors or delays while the fault lasts."
        evidence = [
            f"{exp['id']}: {o['sent']} sent, {o['acked']} acknowledged, {o['stored']} {unit['stored']}, {o['lost']} {unit['lost']}",
            f"errors {o['error_pct']}%, peak p95 {round(o['peak_p95_ms'])} ms, recovery "
            f"{'not observed (bounded recovery window)' if o['recovery_s'] is None else str(o['recovery_s']) + ' s'}",
        ]
        if o["guard_tripped"]:
            evidence.append(f"guard: {o['guard_reason']}")
        evidence += notes
        out.append({"id": "", "service": t, "experiment_id": exp["id"], "severity": _severity(o, notes), "title": title,
                    "evidence": evidence, "impact": impact, "confidence": "high" if runner_kind == "docker" else "medium",
                    "_penalty": p})
    out.sort(key=lambda f: (SEVERITY_ORDER[f["severity"]], -f["_penalty"]))
    for i, f in enumerate(out, 1):
        f["id"] = f"F{i}"
        f.pop("_penalty")
    return out


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", text))


async def findings(results: list[dict], graph: dict, unit: dict, runner_kind: str) -> tuple[list[dict], str, str]:
    base = rules_findings(results, graph, unit, runner_kind)
    if not base:
        return base, "rules", "rules engine"
    prompt = json.dumps({"findings": [{k: f[k] for k in ("id", "service", "severity", "title", "evidence", "impact")}
                                      for f in base]}, separators=(",", ":"))
    system = ("You are a site-reliability engineer. Rewrite each finding's title and impact so an engineer understands "
              "the weakness and why it matters. Keep ids. Use only numbers that appear in that finding's evidence. "
              'Reply JSON only: {"findings":[{"id":"F1","title":"...","impact":"..."}]}')
    by_id = {f["id"]: f for f in base}

    def accept(text: str) -> bool:
        d = llm.extract_json(text) or {}
        rows = d.get("findings")
        if (not isinstance(rows, list) or len(rows) != len(by_id) or not all(isinstance(r, dict) for r in rows)
                or not all(isinstance(r.get("id"), str) for r in rows) or {r.get("id") for r in rows} != set(by_id)):
            return False
        for r in rows:
            allowed = _numbers(" ".join(by_id[r["id"]]["evidence"]) + " " + by_id[r["id"]]["title"])
            if not all(isinstance(r.get(key), str) and r[key].strip() for key in ("title", "impact")):
                return False
            if not _numbers(r["title"] + " " + r["impact"]) <= allowed:
                return False  # invented a number
        return True

    try:
        res = await llm.complete("risk", prompt, system=system, max_tokens=900, min_tier="small", accept=accept)
        for r in (llm.extract_json(res.text) or {})["findings"]:
            by_id[r["id"]]["title"], by_id[r["id"]]["impact"] = str(r["title"]).strip(), str(r["impact"]).strip()
        return base, res.source, res.model
    except llm.NoModelAvailable:
        ledger.record_rule(task="risk", tokens_in=ledger.estimate_tokens(system + prompt),
                           tokens_out=ledger.estimate_tokens(json.dumps(base)))
        return base, "rules", "rules engine"


# ---- fixes -----------------------------------------------------------------------------------------------------
def rules_fixes(found: list[dict], results: list[dict], graph: dict) -> list[dict]:
    nodes = {n["id"]: n for n in graph["nodes"]}
    by_exp = {r["experiment"]["id"]: r for r in score.latest(results)}
    fan = {n["id"]: n["fan_in"] for n in graph["nodes"]}
    fixes: list[dict] = []
    restart_targets: dict[str, list[str]] = {}

    for f in found:
        r = by_exp.get(f["experiment_id"])
        if not r:
            continue
        o, exp = r["outcome"], r["experiment"]
        services = [exp["target"]] + [s for s in nodes if any(s in n for n in r.get("notes", []))]
        if exp["type"] == "compound":
            services += [s.split(":")[1] for s in str(exp["params"].get("steps", "")).split(",") if ":" in s]
        # a restarted dependency breaks its clients' connections (measured: the worker exits), so clients need it too
        clients = [e["from"] for e in graph["edges"] if e["kind"] == "dataflow" and e["to"] in services
                   and nodes.get(e["from"], {}).get("role") in ("worker", "api")]
        unhealed = [s for s in dict.fromkeys(services + clients) if s in nodes and nodes[s]["restart"] not in SELF_HEAL]
        if unhealed and (o["guard_tripped"] or o["recovery_s"] is None or r.get("notes")):
            for s in unhealed:
                restart_targets.setdefault(s, []).append(f["id"])
        if o["lost"] > 0 and any(nodes.get(s, {}).get("role") in ("queue", "cache") for s in services):
            q = next(s for s in services if nodes.get(s, {}).get("role") in ("queue", "cache"))
            redis = "redis" in str(nodes[q].get("image", "")).lower()
            fixes.append({"id": "", "finding_id": f["id"], "title": f"Make the {q} queue durable" + (" (append-only file)" if redis else " (verify broker persistence)"),
                          "change_type": "config",
                          "snippet": (f"services:\n  {q}:\n    command: [\"redis-server\", \"--appendonly\", \"yes\", \"--appendfsync\", \"always\"]\n"
                                     f"    volumes:\n      - {q}-data:/data\nvolumes:\n  {q}-data:") if redis else (
                                     f"# Implementation checklist, not a drop-in config\n# Broker: {q}\n"
                                     "# Verify persistent storage, durable queues and persistent messages.\n"
                                     "# Confirm publishes and acknowledge consumption only after successful processing."),
                          "steps": [f"Verify {q} persistence against the broker's documented configuration.",
                                    "Use the broker's reliable delivery and acknowledgement mechanisms; test crash recovery by id."],
                          "expected_score_gain": round(min(30.0, 20 + 10 * min(1.0, o["lost"] / 50)), 1), "auto_applicable": False})
        if o["peak_p95_ms"] >= 3000 and nodes.get(exp["target"], {}).get("role") in ("queue", "cache", "database"):
            dep = exp["target"]
            is_voting_redis = "vote" in nodes and "redis" in str(nodes[dep].get("image", "")).lower()
            consumers = sorted({e["from"] for e in graph["edges"] if e["kind"] == "dataflow" and e["to"] == dep})
            snippet = ("# vote/app.py\nRedis(host=\"redis\", socket_timeout=1, socket_connect_timeout=1,\n"
                       "      retry_on_timeout=True)  # return HTTP 503 with Retry-After instead of hanging") if is_voting_redis else (
                f"# Implementation checklist, not a drop-in config\n# Dependency: {dep}; callers: {', '.join(consumers) or 'application'}\n"
                "# Bound connection, pool-acquisition and read waits in this application's client library.\n"
                "# Return a controlled unavailable response; make retries bounded and idempotent.\n"
                "# Validate settings against the deployed driver and framework version.")
            fixes.append({"id": "", "finding_id": f["id"], "title": f"Fail fast when {exp['target']} is unreachable",
                          "change_type": "code",
                          "snippet": snippet,
                          "steps": ["Short connect and read timeouts so requests do not hang for seconds.",
                                    "Return 503 with Retry-After, so clients retry instead of waiting."],
                          "expected_score_gain": 5.0, "auto_applicable": False})

    if restart_targets:
        svcs = sorted(restart_targets)
        gain = 0.0
        for r in by_exp.values():
            if r["experiment"]["target"] in svcs:
                p, _ = score.penalty(r, fan.get(r["experiment"]["target"], 0))
                gain = max(gain, p * 0.4)
        fixes.insert(0, {"id": "", "finding_id": restart_targets[svcs[0]][0],
                         "title": f"Add restart: unless-stopped to {', '.join(svcs)}", "change_type": "compose",
                         "snippet": "services:\n" + "".join(f"  {s}:\n    restart: unless-stopped\n" for s in svcs),
                         "steps": ["Docker restarts a crashed container by itself instead of waiting for a human.",
                                   "Applied as docker-compose.fixes.yml on top of the original compose file."],
                         "expected_score_gain": round(gain, 1), "auto_applicable": True, "_services": svcs,
                         "_findings": sorted({fid for ids in restart_targets.values() for fid in ids})})
    unique: dict[str, dict] = {}
    for fx in fixes:  # one fix per title; it still points at the first finding it came from
        unique.setdefault(fx["title"], fx)
    fixes = list(unique.values())
    for i, fx in enumerate(fixes, 1):
        fx["id"] = f"X{i}"
    return fixes


async def recommend(found: list[dict], results: list[dict], graph: dict) -> tuple[list[dict], str, str]:
    fixes = rules_fixes(found, results, graph)
    if not fixes:
        return fixes, "rules", "rules engine"
    prompt = json.dumps({"findings": [{k: f[k] for k in ("id", "title", "impact")} for f in found],
                         "services": list(n["id"] for n in graph["nodes"]),
                         "fixes": [{k: x[k] for k in ("id", "title", "change_type", "snippet", "steps")} for x in fixes]},
                        separators=(",", ":"))
    system = ("You review resilience fixes. For each fix, write 2-3 short concrete steps an engineer follows. Keep ids. "
              "Use only the given application's services, libraries and implementation checklist; do not substitute a different stack. "
              'Reply JSON only: {"fixes":[{"id":"X1","steps":["...","..."]}]}')
    ids = {x["id"] for x in fixes}

    def accept(text: str) -> bool:
        rows = (llm.extract_json(text) or {}).get("fixes")
        return (isinstance(rows, list) and len(rows) == len(ids) and all(isinstance(r, dict) for r in rows)
                and all(isinstance(r.get("id"), str) for r in rows) and {r.get("id") for r in rows} == ids
                and all(isinstance(r.get("steps"), list) and 1 <= len(r["steps"]) <= 4
                        and all(isinstance(s, str) and s.strip() for s in r["steps"]) for r in rows))

    try:
        res = await llm.complete("fix", prompt, system=system, max_tokens=700, min_tier="small", accept=accept)
        steps = {r["id"]: [str(s) for s in r["steps"]] for r in (llm.extract_json(res.text) or {})["fixes"]}
        for x in fixes:
            x["steps"] = steps[x["id"]]
        return fixes, res.source, res.model
    except llm.NoModelAvailable:
        ledger.record_rule(task="fix", tokens_in=ledger.estimate_tokens(system + prompt),
                           tokens_out=ledger.estimate_tokens(json.dumps(fixes)))
        return fixes, "rules", "rules engine"
