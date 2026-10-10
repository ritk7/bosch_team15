"""The exact docker command lines, checked with a fake executor (no Docker needed)."""
from __future__ import annotations

import asyncio
import json

import pytest

from app import targets
from app.docker_runner import DockerRunner, parse_ps


class FakeExec:
    def __init__(self, ps_rows: list[dict]):
        self.calls: list[list[str]] = []
        self.ps_rows = ps_rows

    async def __call__(self, args: list[str], timeout: float = 60.0):
        self.calls.append(args)
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
    tails = [c[len(prefix):] for c in fake.calls]
    assert all(c[: len(prefix)] == prefix for c in fake.calls)
    assert tails == [["kill", "-s", "SIGKILL", "redis"], ["start", "redis"], ["pause", "worker"], ["unpause", "worker"]]


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
