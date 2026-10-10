"""The real-Docker CLI's orchestration, without touching a live Docker sandbox."""
import asyncio
from types import SimpleNamespace

from app import selftest


def test_repeated_selftests_load_history_and_use_unique_device_prefixes(monkeypatch, tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    target = SimpleNamespace(name="hawkbit", title="Test target", unit={"plural": "devices", "stored": "updated", "lost": "stuck"},
                             selftest_experiment="crash-mysql")
    monkeypatch.setattr(selftest.sys, "argv", ["selftest"])
    monkeypatch.setattr(selftest.config, "TARGET_DIR", tmp_path)
    monkeypatch.setattr(selftest.targets, "load", lambda: target)
    monkeypatch.setattr(selftest, "docker_bin", lambda: "docker")
    monkeypatch.setattr(selftest.state, "load_active_fault", lambda: None)
    saved = {"results": [{"run_id": "previous-demo"}]}
    monkeypatch.setattr(selftest.state, "load_snapshot", lambda name: saved if name == "hawkbit" else None)
    ids, loads, events = [], [], []

    class FakeRunner:
        def __init__(self, target):
            pass

        async def up(self):
            events.append("up")

        async def _wait_up(self, svc, max_s):
            pass

        async def services(self):
            return [{"id": "mysql", "state": "running"}]

        async def snapshot(self):
            return {"mysql": {"state": "running"}}

    class FakeEngine:
        order = ["mysql"]
        graph = {"nodes": [{}, {}, {}], "edges": [{"kind": "dataflow"}]}
        phase = "idle"
        guard = SimpleNamespace(armed=False)

        def __init__(self, target, runner):
            self.loaded = False

        async def recover_on_startup(self):
            events.append("recover")

        def load_snapshot(self, snapshot):
            loads.append(snapshot)
            events.append("load")
            self.loaded = True

        async def measure_baseline(self, seconds):
            return {"rps": 2, "error_pct": 0, "p95_ms": 100, "votes_sent": 10, "votes_stored": 10}

        def experiment(self, name):
            return {"target": "mysql"}

        async def run(self, run_id, exp, label, fault_s):
            assert self.loaded
            ids.append(run_id)
            return {"outcome": {"errors": 1, "error_pct": 10, "peak_p95_ms": 100, "guard_tripped": True,
                                "guard_reason": "test", "recovery_s": 1, "lost": 0, "stored": 9, "acked": 9}}

    original_sleep = asyncio.sleep

    async def no_sleep(seconds):
        # Yield once so the CLI's created experiment task can complete.
        await original_sleep(0)

    monkeypatch.setattr(selftest, "DockerRunner", FakeRunner)
    monkeypatch.setattr(selftest, "Engine", FakeEngine)
    monkeypatch.setattr(selftest.asyncio, "sleep", no_sleep)
    assert asyncio.run(selftest.main()) == 0
    assert asyncio.run(selftest.main()) == 0
    assert loads == [saved, saved]
    assert events == ["recover", "load", "up", "recover", "load", "up"]
    assert len(set(ids)) == 2
    assert all(run_id.startswith("selftest-") and len(run_id) == 25 for run_id in ids)
