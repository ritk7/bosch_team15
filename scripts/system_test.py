"""Full system test against a running Chaos Sentinel: every endpoint, end to end, with consistency checks on numbers.
Usage: python scripts/system_test.py http://localhost:8000   (voting app, sim or Docker; resets the demo state)"""
import sys
import time

import httpx

B = sys.argv[1].rstrip("/") + "/api"
c = httpx.Client(timeout=120)
fails = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{': ' + str(detail) if detail else ''}")
    if not ok:
        fails.append(name)


def wait_idle(max_s=600):
    t = time.time()
    time.sleep(1)
    while time.time() - t < max_s:
        s = c.get(f"{B}/state").json()
        if not s["busy"]:
            return s
        time.sleep(1)
    raise TimeoutError


def outcome_consistent(o):
    return (o["sent"] == o["acked"] + o["errors"] and o["stored"] + o["lost"] <= o["acked"]
            and abs(o["error_pct"] - round(100 * o["errors"] / max(1, o["sent"]), 1)) < 0.11
            and abs(o["availability_pct"] - round(100 * o["acked"] / max(1, o["sent"]), 1)) < 0.11
            and o["data_loss"] == (o["lost"] > 0) and (o["guard_reason"] is not None) == o["guard_tripped"])


h = c.get(f"{B}/health").json()
check("health", h["ok"] and h["runner"]["ready"], h["runner"]["detail"])
c.post(f"{B}/reset")
t = c.get(f"{B}/target").json()
cat = c.get(f"{B}/catalog").json()["experiments"]
check("map", len(t["graph"]["nodes"]) == 5 and any(e["kind"] == "dataflow" for e in t["graph"]["edges"]),
      f"{len(t['graph']['nodes'])} services, {len(t['graph']['edges'])} edges")
check("catalog", len(cat) >= 15 and cat == sorted(cat, key=lambda e: -e["risk_hint"]), f"{len(cat)} experiments")
types = {e["type"] for e in cat}
check("failure types", {"crash", "hang", "network_cut", "latency", "packet_loss", "cpu_throttle", "db_overload",
                        "traffic_spike", "compound"} <= types, sorted(types))

b = c.post(f"{B}/baseline", json={"duration_s": 5}).json()
check("baseline: every unit stored, no errors, all healthy",
      b["votes_stored"] == b["votes_sent"] > 0 and b["error_pct"] == 0 and b.get("healthy_services") == b.get("total_services"), b)

p = c.post(f"{B}/predict", json={"experiment_id": "crash-redis"}).json()
check("prediction", {"expected_error_pct", "expected_votes_lost", "expected_recovery_s", "recommended_fault_s",
                     "decided_by"} <= set(p), f"{p['decided_by']}, window {p['recommended_fault_s']} s")

seen = []
for eid in ("crash-redis", "compound-worker-hang-redis-crash", "latency-redis", "crash-result"):
    c.post(f"{B}/run", json={"experiment_id": eid})
    s = wait_idle()
    r = s["results"][-1]
    o = r["outcome"]
    seen.append((eid, o))
    restored = all(x["state"] == "running" for x in s["services"])
    check(f"{eid}: numbers consistent and every service restored", r["experiment"]["id"] == eid and outcome_consistent(o) and restored,
          f"sent {o['sent']} acked {o['acked']} stored {o['stored']} lost {o['lost']} err {o['error_pct']}% "
          f"rec {o['recovery_s']} guard {o['guard_reason']}")
    check(f"{eid}: live state is fresh (not restored)", s.get("restored_from") is None and r["started_at"] > time.time() - 300)

check("results differ between experiments (nothing static)", len({(o["error_pct"], o["lost"], o["recovery_s"]) for _, o in seen}) == len(seen))
check("ghost votes appear only in the combined failure", seen[1][1]["lost"] > 0 and seen[3][1]["lost"] == 0)
check("harmless experiment is harmless", seen[3][1]["errors"] == 0 and not seen[3][1]["guard_tripped"])

a1 = c.post(f"{B}/analyze").json()
a2 = c.post(f"{B}/analyze").json()
check("score deterministic (same results, same score)", a1["score"]["value"] == a2["score"]["value"], a1["score"]["value"])
check("findings cite real experiments", all(f["experiment_id"] in {e for e, _ in seen} for f in a1["findings"]),
      [f["severity"] for f in a1["findings"]])
check("services ranked weakest first", [x["value"] for x in a1["score"]["services"] if x["value"] is not None] ==
      sorted(x["value"] for x in a1["score"]["services"] if x["value"] is not None))

fx = c.post(f"{B}/fixes").json()["fixes"]
auto = [x["id"] for x in fx if x["auto_applicable"]]
check("an applicable fix exists", bool(auto), [x["title"] for x in fx])
ap = c.post(f"{B}/apply-fixes", json={"fix_ids": auto}).json()
check("fix applied", ap.get("ok"), ap.get("detail"))
c.post(f"{B}/rerun", json={})
s = wait_idle()
hist = s["score_history"]
check("re-run produced an after-fix score", hist and hist[-1]["label"] == "after fix", [(h["label"], h["value"]) for h in hist])
check("after-fix score is higher", len(hist) >= 2 and hist[-1]["value"] > hist[0]["value"])
redis_after = [r for r in s["results"] if r["label"] == "after fix" and r["experiment"]["id"] == "crash-redis"]
check("crash-redis recovers by itself after the fix", bool(redis_after) and not redis_after[-1]["outcome"]["guard_tripped"],
      redis_after and redis_after[-1]["outcome"]["recovery_s"])

pl = c.post(f"{B}/plan").json()
check("AI ranked plan covers every experiment", sorted(r["experiment_id"] for r in pl["plan"]) == sorted(e["id"] for e in cat),
      pl["decided_by"])
c.post(f"{B}/twin")
s = wait_idle()
check("twin rehearsed the whole catalog", s["twin"] and len(s["twin"]["rows"]) == len(cat), s["twin"] and s["twin"].get("seconds"))
check("AI vs random computed", s["compare"] and s["compare"]["planner_runs_to_top3"] is not None, s["compare"] and s["compare"]["note"][:90])

rep = c.get(f"{B}/report.md").text
check("report is generated from this session", all(r["experiment"]["title"] in rep for r in s["results"][-3:]) and "Resilience score" in rep)
check("architecture page", "Safety controls" in c.get(f"{B}/architecture.md").text)
m = httpx.get(sys.argv[1].rstrip("/") + "/metrics").text
check("Prometheus metrics", "chaos_service_up" in m and "chaos_resilience_score" in m)
k = c.post(f"{B}/killswitch").json()
check("kill switch when idle", k["ok"])
c.post(f"{B}/reset")
s = c.get(f"{B}/state").json()
check("reset demo clears runs and fixes", not s["results"] and not s["applied_fixes"] and not s["restored_from"])

print(f"\n{'PASS' if not fails else 'FAIL'}: {len(fails)} failed" + (f" ({', '.join(fails)})" if fails else ""))
sys.exit(1 if fails else 0)
