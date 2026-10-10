"""Milestone 1: the guard trips, every experiment restores the original service states, the kill switch works."""
from __future__ import annotations

import asyncio

import pytest

from app import state, targets
from app.campaign import Engine
from app.guard import Guard
from app.runner import SimRunner
from app.sim import Clock

SPEED = 40.0


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "ACTIVE_FAULT", tmp_path / "active_fault.json")
    monkeypatch.setattr(state, "LAST_STATE", tmp_path / "last_state.json")


def make_engine(name: str = "voting") -> Engine:
    target = targets.load(name)
    return Engine(target, SimRunner(target, Clock(SPEED)))


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 60))


# ---- guard (pure code) -----------------------------------------------------------------------------------------
def test_guard_trips_on_each_threshold():
    cases = [dict(error_rate=0.6, completed=10, p95_ms=10, unhealthy=0, wall_s=1),
             dict(error_rate=0.0, completed=10, p95_ms=3000, unhealthy=0, wall_s=1),
             dict(error_rate=0.0, completed=10, p95_ms=10, unhealthy=3, wall_s=1),
             dict(error_rate=0.0, completed=10, p95_ms=10, unhealthy=0, wall_s=46)]
    for case in cases:
        g = Guard()
        g.arm()
        assert g.check(**case) is not None
        assert g.tripped and g.reason


def test_guard_quiet_below_limits_when_disarmed_and_with_few_samples():
    g = Guard()
    calm = dict(error_rate=0.59, completed=10, p95_ms=2999, unhealthy=2, wall_s=45)
    assert g.check(error_rate=1.0, completed=10, p95_ms=9999, unhealthy=5, wall_s=99) is None  # not armed
    g.arm()
    assert g.check(**calm) is None
    assert g.check(error_rate=1.0, completed=4, p95_ms=10, unhealthy=0, wall_s=1) is None  # too few samples
    assert not g.tripped


# ---- engine on the simulation ----------------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["voting", "hawkbit"])
def test_every_experiment_restores_original_states(name):
    async def go():
        e = make_engine(name)
        original = await e.runner.snapshot()
        for exp in e.catalog:
            result = await e.run(f"t{exp['target']}", exp, "test", 4.0)
            assert result is not None, e.error
            assert await e.runner.snapshot() == original, exp["id"]
            assert state.load_active_fault() is None
        return e

    e = run(go())
    assert len(e.results) == len(e.catalog)


def test_crash_redis_trips_guard_and_measures_outcome():
    async def go():
        e = make_engine()
        await e.predict("crash-redis")
        return await e.run("tredis", e.experiment("crash-redis"), "before", 10.0)

    r = run(go())
    o = r["outcome"]
    assert o["guard_tripped"] and ("p95" in o["guard_reason"] or "Error rate" in o["guard_reason"])
    assert o["peak_p95_ms"] >= 3000  # measured on Docker: votes hang while Redis is gone
    assert o["recovery_s"] is not None and o["recovery_s"] > 0
    assert r["prediction"] and r["prediction_error"] is not None
    assert set(r["fleet"]) <= {"ok", "queued", "error", "lost", "pending"}


def test_crash_worker_keeps_answering_and_queues():
    async def go():
        e = make_engine()
        return await e.run("tworker", e.experiment("crash-worker"), "before", 6.0)

    o = run(go())["outcome"]
    assert o["errors"] == 0 and not o["guard_tripped"]
    assert o["recovery_s"] is None or o["recovery_s"] >= 6.0  # votes were delayed until the worker came back


def test_killswitch_mid_run_restores_everything():
    async def go():
        e = make_engine()
        original = await e.runner.snapshot()
        e.start_run("crash-db", fault_s=20.0)
        while not (e.phase == "observe" and e.guard.armed):
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        restored = await e.killswitch()
        return e, original, restored, await e.runner.snapshot()

    e, original, restored, after = run(go())
    assert after == original
    assert "db" in restored
    assert e.error == "Stopped by the kill switch" and not e.busy
    assert state.load_active_fault() is None


def test_active_fault_is_restored_on_startup():
    async def go():
        e = make_engine()
        original = await e.runner.snapshot()
        state.save_active_fault({"run_id": "x", "experiment_id": "crash-redis", "original": original})
        await e.runner.crash("redis")
        restored = await e.recover_on_startup()
        return restored, await e.runner.snapshot(), original

    restored, after, original = run(go())
    assert restored == ["redis"] and after == original
    assert state.load_active_fault() is None


def test_runner_refuses_services_outside_the_compose_project():
    async def go():
        await make_engine().runner.crash("postgres-of-another-team")

    with pytest.raises(ValueError):
        run(go())


def test_map_has_five_services_and_dataflow():
    g = targets.load("voting").graph()
    assert sorted(n["id"] for n in g["nodes"]) == ["db", "redis", "result", "vote", "worker"]
    nodes = {n["id"]: n for n in g["nodes"]}
    assert nodes["redis"]["fan_in"] == 2 and nodes["redis"]["spof"] and nodes["redis"]["restart"] is None
    assert {("vote", "redis"), ("worker", "redis"), ("worker", "db"), ("result", "db")} <= \
        {(e["from"], e["to"]) for e in g["edges"] if e["kind"] == "dataflow"}


def test_compound_failure_loses_acknowledged_votes_silently():
    async def go():
        e = make_engine()
        return await e.run("tghost", e.experiment("compound-worker-hang-redis-crash"), "before", 10.0)

    r = run(go())
    o = r["outcome"]
    assert o["lost"] > 0 and o["data_loss"]  # acknowledged with HTTP 200, then gone
    assert any("worker" in n for n in r["notes"])  # measured on Docker: the worker exits on reconnect
    assert o["acked"] >= o["stored"] + o["lost"]


def test_hawkbit_map_and_units():
    t = targets.load("hawkbit")
    g = t.graph()
    assert sorted(n["id"] for n in g["nodes"]) == ["hawkbit", "mysql", "rabbitmq"]
    assert {n["id"]: n["restart"] for n in g["nodes"]} == {"hawkbit": "always", "mysql": "always", "rabbitmq": "on-failure"}
    assert t.unit["plural"] == "devices"
