"""Routes return the shapes in docs/CONTRACT.md."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app import config, main, state
from app.runner import SimRunner
from app.sim import Clock

STATE_KEYS = {"runner", "services", "baseline", "phase", "current", "results", "campaign", "analysis", "fixes",
              "applied_fixes", "score_history", "compare", "cost", "error"}
NODE_KEYS = {"id", "role", "image", "ports", "healthcheck", "restart", "stateful", "replicas", "depends_on", "fan_in",
             "spof", "notes"}
OUTCOME_KEYS = {"sent", "acked", "stored", "lost", "errors", "error_pct", "p95_ms", "peak_p95_ms", "recovery_s",
                "availability_pct", "guard_tripped", "guard_reason", "data_loss"}
PREDICTION_KEYS = {"experiment_id", "summary", "expected_error_pct", "expected_votes_lost", "expected_recovery_s",
                   "confidence", "reason", "hypothesis", "decided_by", "model"}


def test_contract_shapes_and_a_full_run(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "ACTIVE_FAULT", tmp_path / "a.json")
    monkeypatch.setattr(state, "LAST_STATE", tmp_path / "l.json")
    monkeypatch.setattr(config, "RUNNER", "sim")  # never touch a real Docker project from the tests
    with TestClient(main.app) as client:
        main.engine.runner = SimRunner(main.engine.target, Clock(40))  # fast simulated time
        main.engine.clock = main.engine.runner.clock

        h = client.get("/api/health").json()
        assert h["ok"] and h["runner"]["kind"] == "sim" and set(h["tiers"]) == {"local", "small", "large"}

        t = client.get("/api/target").json()
        assert len(t["graph"]["nodes"]) == 5 and set(t["graph"]["nodes"][0]) == NODE_KEYS
        assert t["unit"]["plural"] == "votes" and len(t["services"]) == 5

        cat = client.get("/api/catalog").json()["experiments"]
        assert {"crash-redis", "hang-redis", "compound-worker-hang-redis-crash"} <= {e["id"] for e in cat}
        assert cat == sorted(cat, key=lambda e: -e["risk_hint"])

        assert client.post("/api/predict", json={"experiment_id": "nope"}).status_code == 404
        p = client.post("/api/predict", json={"experiment_id": "crash-redis"}).json()
        assert set(p) == PREDICTION_KEYS

        run_id = client.post("/api/run", json={"experiment_id": "crash-redis", "fault_duration_s": 5}).json()["run_id"]
        assert client.post("/api/run", json={"experiment_id": "crash-db"}).status_code == 409
        deadline = time.time() + 30
        s = client.get("/api/state").json()
        while s["phase"] != "done" and time.time() < deadline:
            time.sleep(0.05)
            s = client.get("/api/state").json()
        assert set(s) == STATE_KEYS and s["phase"] == "done", s["error"]
        assert s["current"]["run_id"] == run_id and set(s["current"]["guard"]["thresholds"]) == \
            {"error_rate", "p95_ms", "unhealthy", "max_wall_s"}
        r = s["results"][-1]
        assert set(r["outcome"]) == OUTCOME_KEYS and r["prediction"]["experiment_id"] == "crash-redis"
        assert all(x["state"] == "running" for x in s["services"])

        assert client.post("/api/killswitch").json() == {"ok": True, "restored": []}
        assert client.get("/api/snapshot").json()["results"]
