"""Milestone 1: the guard trips, every experiment restores the original service states, the kill switch works."""
from __future__ import annotations

import asyncio

import pytest

from app import state, targets
from app.campaign import Engine
from app.guard import Guard
from app.runner import SimRunner
from app.sim import Clock

SPEED = 20.0


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


def test_traffic_spike_while_worker_down_raises_rate_then_restores():
    async def go():
        e = make_engine()
        r = await e.run("tspike", e.experiment("compound-worker-crash-traffic-spike"), "before", 8.0)
        return e, r

    e, r = run(go())
    assert r["outcome"]["sent"] > 60  # 6x traffic for part of the window
    assert r["outcome"]["errors"] == 0  # vote keeps answering; votes queue up


def test_blast_radius_ignores_services_faulted_on_purpose():
    async def go():
        e = make_engine()
        return await e.run("tghost2", e.experiment("compound-worker-hang-redis-crash"), "before", 6.0)

    o = run(go())["outcome"]
    assert not (o["guard_reason"] or "").startswith("2 other services")  # worker + redis are the experiment itself


def test_guard_latency_limit_is_relative_to_baseline():
    g = Guard()
    g.arm(baseline_p95_ms=500)
    assert g.thresholds.p95_ms == 5000  # 10x a slow baseline
    g.arm(baseline_p95_ms=20)
    assert g.thresholds.p95_ms == 3000  # never below the 3 s floor


def test_db_overload_and_restore():
    async def go():
        e = make_engine()
        original = await e.runner.snapshot()
        r = await e.run("tload", e.experiment("db-overload-db"), "before", 5.0)
        return r, original, await e.runner.snapshot()

    r, original, after = run(go())
    assert r is not None and after == original


def test_manual_map_correction(tmp_path, monkeypatch):
    from app import config, mapper

    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    t = targets.load("voting")
    t.target_dir = tmp_path / "not-cloned"
    mapper.save_corrections(t.state_dir(), {"add": [{"from": "result", "to": "redis", "label": "debug read"}],
                                       "remove": [("worker", "db")]})
    pairs = {(e["from"], e["to"]) for e in t.graph()["edges"] if e["kind"] == "dataflow"}
    assert ("result", "redis") in pairs and ("worker", "db") not in pairs


def test_latency_and_packet_loss_are_injected_and_removed():
    async def go():
        e = make_engine()
        original = await e.runner.snapshot()
        r1 = await e.run("tlat", e.experiment("latency-redis"), "before", 5.0)
        mid = await e.runner.snapshot()
        r2 = await e.run("tloss", e.experiment("packet-loss-redis"), "before", 5.0)
        return r1, r2, original, mid, await e.runner.snapshot()

    r1, r2, original, mid, after = run(go())
    assert r1["outcome"]["peak_p95_ms"] > 2000  # +500 ms per round trip on the vote -> redis path
    assert mid == original and after == original and original["redis"]["net"] is None


def test_planner_number_check_allows_rounding_but_not_invention():
    from app.planner import numbers_ok

    src = '{"risk":0.85,"lost":22,"recovery_s":7.8}'
    assert numbers_ok("risk 0.85, 22 votes lost, about 8 s", src)
    assert numbers_ok("85% risk", src)
    assert not numbers_ok("90% error rate", src)


def test_fixes_and_map_corrections_work_without_a_cloned_app(tmp_path, monkeypatch):
    """Fresh clone in simulation mode: targets/voting does not exist. Saving must not create it (setup.sh would then
    skip cloning) and must not crash."""
    from app import config
    from app.targets.voting import VotingTarget

    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    missing = tmp_path / "targets" / "voting"
    t = VotingTarget(missing)
    assert VotingTarget.state_dir(t) == tmp_path / "cache" / "voting"
    assert not missing.exists()


def test_twin_rehearses_the_whole_catalog():
    async def go():
        e = make_engine()
        tw = await e.run_twin(speed=60)
        return e, tw

    e, tw = run(go())
    assert len(tw["rows"]) == len(e.catalog)  # a placeholder baseline once made every rehearsal fail the health gate
    assert e.compare and e.compare["planner_runs_to_top3"] is not None


@pytest.mark.parametrize("rate", [1, 2, 5])
def test_steady_gate_is_rate_aware(rate):
    from app.probe import steady_check, steady_span

    span = steady_span(rate)
    healthy = {"completed": int(rate * span) - 1, "error_rate": 0.0, "p95_ms": 120.0}  # one answer still in flight
    assert steady_check(healthy, rate, 100.0)[0], (rate, healthy)
    slow_but_healthy = {**healthy, "p95_ms": 900.0}
    assert steady_check(slow_but_healthy, rate, 386.0)[0]  # hawkBit under emulation
    assert not steady_check({**healthy, "error_rate": 0.25}, rate, 100.0)[0]
    assert not steady_check({**healthy, "p95_ms": 5000.0}, rate, 100.0)[0]
    assert not steady_check({**healthy, "completed": 1}, rate, 100.0)[0]


def test_low_probe_rate_runs_an_experiment():
    async def go():
        t = targets.load("voting")
        e = Engine(t, SimRunner(t, Clock(SPEED)), rate=2)
        return await e.run("tslow", e.experiment("crash-result"), "before", 4.0), e

    r, e = run(go())
    assert r is not None, e.error  # 2 units/s used to fail the steady gate forever


def test_targets_have_their_own_default_rate(monkeypatch):
    from app import config

    monkeypatch.setattr(config, "PROBE_RATE_SET", False)
    t = targets.load("hawkbit")
    assert Engine(t, SimRunner(t, Clock(SPEED))).rate == 2


def test_refused_start_never_marks_inject_as_reached():
    async def go():
        e = make_engine()
        e.baseline = {"rps": 5, "error_pct": 50.0, "p95_ms": 10, "votes_sent": 10, "votes_stored": 5}
        r = await e.run("trefused", e.experiment("crash-redis"), "before", 4.0)
        return r, e

    r, e = run(go())
    assert r is None and "inject" not in e.current.get("phases", [])


def test_twin_reports_failed_rehearsals_and_skips_biased_comparison(monkeypatch):
    from app import campaign

    real_run = campaign.Engine.run
    calls = {"n": 0}

    async def flaky(self, run_id, exp, label, fault_s):
        calls["n"] += 1
        if self.persist is False and calls["n"] % 3 == 0:
            self.error = "simulated failure"
            return None
        return await real_run(self, run_id, exp, label, fault_s)

    monkeypatch.setattr(campaign.Engine, "run", flaky)

    async def go():
        e = make_engine()
        return await e.run_twin(speed=60), e

    tw, e = run(go())
    assert tw["failed"] and tw["completed"] + len(tw["failed"]) == tw["attempted"]
    assert e.compare["planner_runs_to_top3"] is None and "Not computed" in e.compare["note"]


def test_wedged_application_is_repaired_and_reported():
    """Measured on real hawkBit: after MySQL latency the containers ran but the API stayed dead until restarted."""
    async def go():
        e = make_engine("hawkbit")
        r = await e.run("twedge", e.experiment("latency-mysql"), "before", 6.0)
        ok, why = await e.target.app_ready(e.runner)
        return r, ok, e

    r, ok, e = run(go())
    assert r is not None, e.error
    assert any("stayed unresponsive" in n for n in r["notes"])  # reported as a secondary failure
    assert ok  # and the tool left the application answering
    assert e.baseline is None  # a fresh baseline is measured before the next experiment


def test_preflight_refuses_when_the_application_does_not_answer():
    async def go():
        e = make_engine("hawkbit")
        e.runner.app.wedged = True
        e._app_ready_within = lambda max_s: asyncio.sleep(0, result=False)
        return await e.run("tnoapp", e.experiment("crash-mysql"), "before", 4.0), e

    r, e = run(go())
    assert r is None and "not answering" in (e.error or "") and "inject" not in e.current.get("phases", [])


def test_explanation_matches_parsed_restart_policy():
    from app import agents

    t = targets.load("hawkbit")
    g = t.graph()
    exp = next(x for x in Engine(t, SimRunner(t, Clock(SPEED))).catalog if x["id"] == "compound-rabbitmq-down-mysql-crash")
    p = agents.rules_prediction(exp, g, entry=t.entry, sync={"hawkbit", "mysql", "rabbitmq"}, rate=2, fault_s=6,
                                history=[], unit_plural="devices")
    assert "no restart policy" not in p["reason"] and "always" in p["reason"]


def test_hawkbit_map_is_derived_from_its_compose_settings():
    """Cross-check: with no hand-written edges, the compose file alone yields hawkBit's two real data flows, and a
    database merely named 'hawkbit' does not create a false arrow."""
    from app import mapper

    t = targets.load("hawkbit")
    g = mapper.build_graph(t.compose_file(), roles=t.roles, extra_edges=[], notes={})
    flows = {(e["from"], e["to"]) for e in g["edges"] if e["kind"] == "dataflow"}
    assert flows == {("hawkbit", "mysql"), ("hawkbit", "rabbitmq")}
    assert sorted(n["id"] for n in g["nodes"]) == ["hawkbit", "mysql", "rabbitmq"]
