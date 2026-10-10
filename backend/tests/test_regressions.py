import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main, state, targets
from app.campaign import Busy, Engine
from app.runner import SimRunner
from app.probe import Probe
from app.sim import Clock


@pytest.fixture(autouse=True)
def isolated_files(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "ACTIVE_FAULT", tmp_path / "fault.json")
    monkeypatch.setattr(state, "LAST_STATE", tmp_path / "snapshot.json")


def engine(name="voting", persist=True):
    target = targets.load(name)
    return Engine(target, SimRunner(target, Clock(40)), persist=persist)


@pytest.mark.parametrize("route,key,value", [
    ("baseline", "duration_s", v) for v in ["abc", "NaN", "Infinity", [], {}, False, 0, 61]
] + [
    ("run", "fault_duration_s", v) for v in ["abc", "NaN", [], False, 0, 31]
] + [
    ("campaign/start", "budget", v) for v in ["abc", [], False, 0, 2.5, 1000]
] + [
    ("campaign/start", "seed", v) for v in ["abc", {}, False, -1, 2.5]
])
def test_invalid_numeric_input_returns_422_not_500(route, key, value, monkeypatch):
    monkeypatch.setattr(main, "engine", engine(persist=False))
    client = TestClient(main.app, raise_server_exceptions=False)  # no lifespan; retain the isolated simulator
    response = client.post(f"/api/{route}", json={"experiment_id": "crash-redis", key: value})
    assert response.status_code == 422, response.text


def test_seed_zero_is_preserved(monkeypatch):
    e = engine(persist=False)
    monkeypatch.setattr(main, "engine", e)
    captured = []
    monkeypatch.setattr(e, "start_campaign", lambda budget, strategy, seed: captured.append(seed))
    client = TestClient(main.app)
    assert client.post("/api/campaign/start", json={"seed": 0}).status_code == 200
    assert captured == [0]


def test_baseline_reserves_engine_against_runs_switches_and_other_baselines(monkeypatch):
    async def go():
        e = engine(persist=False)
        monkeypatch.setattr(main, "engine", e)
        started, release = asyncio.Event(), asyncio.Event()
        async def measure(duration):
            started.set()
            await release.wait()
            return {"rps": 5, "error_pct": 0, "p95_ms": 20}
        monkeypatch.setattr(e, "measure_baseline", measure)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            baseline = asyncio.create_task(client.post("/api/baseline", json={}))
            await started.wait()
            assert e.busy
            assert (await client.post("/api/run", json={"experiment_id": "crash-redis"})).status_code == 409
            assert (await client.post("/api/target/select", json={"name": "hawkbit"})).status_code == 409
            assert (await client.post("/api/baseline", json={})).status_code == 409
            release.set()
            assert (await baseline).status_code == 200
            assert not e.busy
    asyncio.run(go())


def test_unhealthy_baseline_blocks_fault_injection():
    async def go():
        e = engine()
        e.baseline = {"rps": 5, "error_pct": 0, "p95_ms": 20, "votes_sent": 25, "votes_stored": 20}
        original = await e.runner.snapshot()
        result = await e.run("bad", e.experiment("crash-redis"), "test", 4)
        assert result is None and "Baseline is not healthy" in e.error
        assert await e.runner.snapshot() == original
        assert state.load_active_fault() is None
    asyncio.run(go())


def test_slow_verified_restore_does_not_consume_recovery_observation_window(monkeypatch):
    async def go():
        e = engine(persist=False)
        restore = e.runner.restore_all
        checked = []

        async def slow_restore(original, order):
            restored = await restore(original, order)
            await e.clock.sleep(50)  # health verification alone exceeded the armed-fault wall limit
            return restored

        def recovered(probe, since, now, need=5):
            checked.append((since, now, e.guard.armed))
            return True

        monkeypatch.setattr(e.runner, "restore_all", slow_restore)
        monkeypatch.setattr(Probe, "recovered_since", recovered)
        result = await e.run("slow-restore", e.experiment("crash-worker"), "test", 1)
        assert result is not None, e.error
        assert checked and all(not armed for _, _, armed in checked)

    asyncio.run(go())


def test_new_baseline_does_not_show_previous_guard_trip(monkeypatch):
    async def go():
        e = engine()
        e.guard.tripped, e.guard.reason = True, "old failure"
        started, release = asyncio.Event(), asyncio.Event()
        async def measure():
            started.set()
            await release.wait()
        monkeypatch.setattr(e, "measure_baseline", measure)
        e.start_run("crash-redis")
        await started.wait()
        assert not e.guard.tripped and e.guard.reason is None and not e.guard.armed
        e.task.cancel()
        await asyncio.gather(e.task, return_exceptions=True)
    asyncio.run(go())


def test_twin_restore_cannot_clear_a_real_active_fault():
    async def go():
        state.save_active_fault({"original": {"redis": {}}, "target": "voting", "runner": "docker"})
        e = engine(persist=False)
        assert await e.recover_on_startup() == []
        await e._restore(await e.runner.snapshot())
        assert state.load_active_fault()["runner"] == "docker"
    asyncio.run(go())


@pytest.mark.parametrize("fault", [
    {"target": "hawkbit", "runner": "sim", "original": {"mysql": {}}},
    {"target": "voting", "runner": "docker", "original": {"redis": {}}},
    {"original": {"someone-elses-db": {}}},
])
def test_recovery_refuses_mismatched_scope_and_keeps_record(fault):
    async def go():
        state.save_active_fault(fault)
        e = engine()
        with pytest.raises(RuntimeError, match="another target/runner"):
            await e.recover_on_startup()
        assert state.load_active_fault() == fault
        with pytest.raises(Busy):
            e.start_run("crash-redis")
    asyncio.run(go())


def test_failed_restore_keeps_recovery_record_and_surfaces_error(monkeypatch):
    async def go():
        e = engine()
        async def fail(original, order):
            raise RuntimeError("Docker refused restart")
        monkeypatch.setattr(e.runner, "restore_all", fail)
        result = await e.run("restorefail", e.experiment("crash-redis"), "test", 2)
        assert result is None
        assert "restoration failed" in e.error and "Docker refused restart" in e.error
        assert state.load_active_fault()["target"] == "voting"
        assert not e.guard.armed
    asyncio.run(go())


def test_fault_end_and_guard_survive_snapshot_without_reusing_stale_baseline():
    async def go():
        e = engine()
        result = await e.run("saved", e.experiment("crash-redis"), "test", 10)
        assert result and result["fault_ended_s"] > 0 and result["outcome"]["guard_tripped"]
        snapshot = await e.state_json()
        next_engine = engine()
        assert next_engine.load_snapshot(snapshot)
        assert next_engine.baseline is None
        assert next_engine.guard.tripped and next_engine.guard.reason
        assert next_engine.current["fault_ended_s"] == result["fault_ended_s"]
        assert next_engine.current["fault_duration_s"] == 10
    asyncio.run(go())


def test_compound_second_step_failure_is_reported_and_first_step_restored(monkeypatch):
    async def go():
        e = engine()
        original = await e.runner.snapshot()
        async def fail(svc):
            raise RuntimeError("second fault failed")
        monkeypatch.setattr(e.runner, "crash", fail)
        result = await e.run("compoundfail", e.experiment("compound-worker-hang-redis-crash"), "test", 6)
        assert result is None and "second fault failed" in e.error
        assert await e.runner.snapshot() == original
        assert state.load_active_fault() is None
    asyncio.run(go())


def test_campaign_stops_after_failed_run_instead_of_injecting_again(monkeypatch):
    async def go():
        e = engine(persist=False)
        attempts = []
        async def predict(exp_id):
            return {}
        async def run(*args):
            attempts.append(args)
            e.error = "restoration failed"
            return None
        monkeypatch.setattr(e, "predict", predict)
        monkeypatch.setattr(e, "run", run)
        e.start_campaign(3, "random")
        await e.task
        assert len(attempts) == 1 and e.campaign["status"] == "stopped"
    asyncio.run(go())


def test_legacy_saved_restart_fix_keeps_id_and_recovers_executable_targets(tmp_path, monkeypatch):
    async def go():
        e = engine()
        snapshot = await e.state_json()
        snapshot["analysis"] = {"findings": [{"id": "F1", "service": "worker", "experiment_id": "crash-worker"}]}
        snapshot["fixes"] = {"fixes": [{"id": "X7", "finding_id": "F1", "title": "Restart worker",
                                      "auto_applicable": True, "change_type": "compose",
                                      "snippet": "services:\n  worker:\n    restart: unless-stopped\n"}]}
        restored = engine()
        assert restored.load_snapshot(snapshot)
        fix = restored.fixes["fixes"][0]
        assert fix["id"] == "X7" and fix["_services"] == ["worker"]
        overlay = tmp_path / "fixes.yml"
        monkeypatch.setattr(restored.target, "overlay_file", lambda: overlay)
        await restored.apply_fixes(["X7"])
        assert "unless-stopped" in overlay.read_text() and restored.applied_fixes == ["X7"]
        assert restored.runner.app.svcs["worker"].restart_policy == "unless-stopped"
    asyncio.run(go())


def test_saved_unknown_service_fix_cannot_be_applied():
    async def go():
        e = engine()
        snapshot = await e.state_json()
        snapshot["fixes"] = {"fixes": [{"id": "X1", "finding_id": "F1", "title": "Unsafe",
            "auto_applicable": True, "snippet": "services:\n  other-project-db:\n    restart: unless-stopped\n"}]}
        assert e.load_snapshot(snapshot)
        assert not e.fixes["fixes"][0]["auto_applicable"]
        with pytest.raises(ValueError, match="advice only"):
            await e.apply_fixes(["X1"])
    asyncio.run(go())


def test_legacy_mysql_advice_is_revalidated_without_changing_fix_id():
    async def go():
        e = engine("hawkbit")
        exp = e.experiment("crash-mysql")
        e.results = [{"run_id": "legacy", "label": "before", "prediction": None, "experiment": exp,
                      "timeline": [], "fleet": [], "outcome": {"sent": 25, "acked": 25, "stored": 25, "lost": 0,
                       "errors": 0, "error_pct": 0, "recovery_s": 3, "peak_p95_ms": 6000,
                       "guard_tripped": True, "guard_reason": "p95"}}]
        e.analysis = {"findings": [{"id": "F1", "service": "mysql", "experiment_id": "crash-mysql"}]}
        e.fixes = {"fixes": [{"id": "X9", "finding_id": "F1", "title": "Fail fast when mysql is unreachable",
                            "auto_applicable": False, "snippet": "# vote/app.py\nRedis(host='redis')"}]}
        snapshot = await e.state_json()
        restored = engine("hawkbit")
        assert restored.load_snapshot(snapshot)
        fix = restored.fixes["fixes"][0]
        assert fix["id"] == "X9" and "Redis(" not in fix["snippet"] and "mysql" in fix["snippet"]
    asyncio.run(go())
