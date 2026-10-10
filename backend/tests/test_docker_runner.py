"""The exact docker command lines, checked with a fake executor (no Docker needed)."""
from __future__ import annotations

import asyncio
import json

import pytest

from app import targets
from app.docker_runner import DockerRunner, parse_ps, run_cmd


class FakeExec:
    def __init__(self, ps_rows: list[dict]):
        self.calls: list[list[str]] = []
        self.ps_rows = ps_rows

    async def __call__(self, args: list[str], timeout: float = 60.0):
        self.calls.append(args)
        if args[1:3] == ["inspect", "-f"]:
            return 0, "4242", ""
        if args[1:2] == ["info"]:
            return 0, "8", ""
        if "ps" in args:
            return 0, "\n".join(json.dumps(r) for r in self.ps_rows), ""
        return 0, "", ""


def make(rows=None):
    rows = rows if rows is not None else [{"Service": s, "Name": f"voting-{s}-1", "State": "running", "Health": ""}
                                          for s in ("vote", "redis", "worker", "db", "result")]
    fake = FakeExec(rows)
    r = DockerRunner(targets.load("voting"), project="voting", executor=fake)
    r.docker = "docker"
    return r, fake


def test_parse_ps_handles_array_and_ndjson():
    rows = [{"Service": "a"}, {"Service": "b"}]
    assert parse_ps(json.dumps(rows)) == rows
    assert parse_ps("\n".join(json.dumps(r) for r in rows)) == rows
    assert parse_ps("") == []


def test_commands_are_scoped_to_the_project():
    r, fake = make()
    asyncio.run(r.crash("redis"))
    asyncio.run(r.start("redis"))
    asyncio.run(r.pause("worker"))
    asyncio.run(r.unpause("worker"))
    prefix = ["docker", "compose", "-p", "voting", "-f", str(r.compose)]
    compose = [c[len(prefix):] for c in fake.calls if c[:2] == ["docker", "compose"]]
    assert all(c[: len(prefix)] == prefix for c in fake.calls if c[:2] == ["docker", "compose"])
    actions = [c for c in compose if c[0] != "ps"]
    assert actions == [["start", "redis"], ["pause", "worker"], ["unpause", "worker"]]
    # crash = SIGKILL of exactly this container's process (pid from inspect), so a restart policy can still fire
    assert ["docker", "run", "--rm", "--privileged", "--pid=host", "alpine:3.20", "kill", "-9", "4242"] in fake.calls


def test_business_check_commands():
    r, fake = make()
    asyncio.run(r.client().stored_ids("cs-r1-"))
    asyncio.run(r.client().queued_ids("cs-r1-"))
    prefix = len(["docker", "compose", "-p", "voting", "-f", str(r.compose)])
    assert fake.calls[0][prefix:] == ["exec", "-T", "db", "psql", "-U", "postgres", "-At", "-c",
                                      "select id from votes where id like 'cs-r1-%'"]
    assert fake.calls[1][prefix:] == ["exec", "-T", "redis", "redis-cli", "--raw", "LRANGE", "votes", "0", "-1"]


def test_refuses_unknown_services():
    r, fake = make()
    with pytest.raises(ValueError):
        asyncio.run(r.crash("someone-elses-db"))
    assert fake.calls == []


def test_restore_starts_stopped_services_dependencies_first():
    rows = [{"Service": s, "Name": f"voting-{s}-1", "State": "exited" if s in ("redis", "worker") else "running", "Health": ""}
            for s in ("vote", "redis", "worker", "db", "result")]
    r, fake = make(rows)
    original = {s: {"state": "running", "connected": True, "cpus": None} for s in r.allowed}

    async def go():
        async def fake_wait(svc, max_s=25.0):
            for row in rows:
                if row["Service"] == svc:
                    row["State"] = "running"
        r._wait_up = fake_wait
        return await r.restore_all(original, ["redis", "db", "vote", "result", "worker"])

    assert asyncio.run(go()) == ["redis", "worker"]
    starts = [c[-1] for c in fake.calls if "start" in c]
    assert starts == ["redis", "worker"]


def test_netem_runs_only_in_the_target_container_namespace():
    r, fake = make()
    asyncio.run(r.set_net("redis", "delay 500ms"))
    run_call = next(c for c in fake.calls if c[1] == "run")
    assert run_call[2:8] == ["--rm", "--net", "container:voting-redis-1", "--cap-add", "NET_ADMIN", "nicolaka/netshoot:v0.13"]
    assert "netem delay 500ms" in run_call[-1]
    with pytest.raises(ValueError):
        asyncio.run(r.set_net("redis", "delay 5ms; rm -rf /"))


def test_cpu_limit_is_lifted_to_all_cpus_not_zero():
    r, fake = make()
    asyncio.run(r.set_cpus("db", 0.1))
    asyncio.run(r.set_cpus("db", None))
    updates = [c for c in fake.calls if c[1] == "update"]
    assert updates[0][2:4] == ["--cpus", "0.1"]
    assert updates[1][2:4] == ["--cpus", "8"]  # `--cpus 0` silently keeps the old limit (measured)


def test_partial_or_unhealthy_stack_is_not_ready():
    r, _ = make([{"Service": "vote", "Name": "voting-vote-1", "State": "running", "Health": ""}])
    assert not asyncio.run(r.ready())[0]
    r, fake = make()
    fake.ps_rows[0]["Health"] = "unhealthy"
    assert not asyncio.run(r.ready())[0]


def test_wait_timeout_and_repeated_exit_are_failures(monkeypatch):
    r, _ = make([{"Service": "redis", "State": "exited"}])
    async def no_sleep(_):
        pass
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    with pytest.raises(RuntimeError, match="exited repeatedly"):
        asyncio.run(r._wait_up("redis", 25))
    with pytest.raises(RuntimeError, match="did not become ready"):
        asyncio.run(r._wait_up("redis", 0))


def test_restore_allows_default_30_second_healthcheck_interval(monkeypatch):
    rows = [{"Service": "mysql", "Name": "hawkbit-mysql-1", "State": "running", "Health": "starting"}]
    fake = FakeExec(rows)
    r = DockerRunner(targets.load("hawkbit"), executor=fake)
    elapsed = [0.0]

    async def go():
        monkeypatch.setattr(asyncio.get_running_loop(), "time", lambda: elapsed[0])

        async def sleep(seconds):
            elapsed[0] += seconds
            if elapsed[0] >= 30:
                rows[0]["Health"] = "healthy"

        monkeypatch.setattr(asyncio, "sleep", sleep)
        await r.restore_all({"mysql": {"state": "running", "connected": True, "cpus": None}}, ["mysql"])

    asyncio.run(go())
    assert elapsed[0] == 30
    assert rows[0]["Health"] == "healthy"


def test_restore_attempts_remaining_services_after_one_failure():
    rows = [{"Service": s, "Name": f"voting-{s}-1", "State": "exited", "Health": ""}
            for s in ("redis", "worker")]
    r, fake = make(rows)
    original = {s: {"state": "running", "connected": True, "cpus": None} for s in ("redis", "worker")}
    async def go():
        async def wait(svc, max_s=25):
            if svc == "redis":
                raise RuntimeError("unhealthy dependency")
        r._wait_up = wait
        with pytest.raises(RuntimeError, match="Restoration incomplete.*redis"):
            await r.restore_all(original, ["redis", "worker"])
    asyncio.run(go())
    assert [c[-1] for c in fake.calls if "start" in c] == ["redis", "worker"]


def test_unchanged_but_unhealthy_container_is_not_silently_accepted():
    r, _ = make([{"Service": "redis", "Name": "voting-redis-1", "State": "running", "Health": "unhealthy"}])
    async def go():
        async def wait(svc, max_s=25):
            raise RuntimeError("still unhealthy")
        r._wait_up = wait
        with pytest.raises(RuntimeError, match="Restoration incomplete.*still unhealthy"):
            await r.restore_all({"redis": {"state": "running", "connected": True, "cpus": None}}, ["redis"])
    asyncio.run(go())


def test_partial_network_failure_remains_restorable_and_reconnect_is_idempotent():
    r, _ = make()
    attached = {"voting_default", "voting_back-tier", "other-project_network"}
    calls = []
    async def networks(_):
        return sorted(attached)
    async def executor(args, timeout=60):
        calls.append(args)
        if args[1:3] == ["network", "disconnect"]:
            if args[3] == "voting_default":
                return 1, "", "disconnect refused"
            attached.remove(args[3])
            return 0, "", ""
        if args[1:3] == ["network", "connect"]:
            attached.add(args[-2])
            return 0, "", ""
        if "ps" in args:
            return 0, json.dumps([{"Service": "redis", "Name": "voting-redis-1", "State": "running"}]), ""
        return 0, "", ""
    r.exe, r._networks = executor, networks
    async def go():
        with pytest.raises(RuntimeError, match="disconnect refused"):
            await r.disconnect("redis")
        assert not r.connected["redis"]
        assert "voting_back-tier" not in attached
        await r.connect("redis")
        assert r.connected["redis"] and "voting_back-tier" in attached
        await r.connect("redis")
    asyncio.run(go())
    assert len([c for c in calls if c[1:3] == ["network", "connect"]]) == 1
    assert all("other-project_network" not in c for c in calls)


def test_network_failure_does_not_claim_success():
    r, _ = make()
    async def networks(_):
        return []
    r._networks = networks
    with pytest.raises(RuntimeError, match="No project-scoped network"):
        asyncio.run(r.disconnect("redis"))
    assert r.connected["redis"]


def test_failed_docker_inspection_is_not_an_empty_healthy_state():
    r, _ = make()
    async def fail(args, timeout=60):
        return 1, "", "Docker is unavailable"
    r.exe = fail
    with pytest.raises(RuntimeError, match="Cannot inspect Docker"):
        asyncio.run(r.snapshot())


@pytest.mark.parametrize("cancel", [False, True])
def test_timed_out_or_canceled_cli_children_are_reaped(monkeypatch, cancel):
    async def go():
        started = asyncio.Event()
        class Process:
            returncode = None
            calls = 0
            killed = False
            async def communicate(self):
                self.calls += 1
                if self.calls == 1:
                    started.set()
                    await asyncio.Future()
                return b"", b""
            def kill(self):
                self.killed, self.returncode = True, -9
        proc = Process()
        async def create(*args, **kwargs):
            return proc
        monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
        task = asyncio.create_task(run_cmd(["fake"], .01 if not cancel else 60))
        await started.wait()
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert (await task)[0] == 124
        assert proc.killed and proc.calls == 2
    asyncio.run(go())
