"""In-memory simulation of the voting app, used with RUNNER=sim (tests, building, stage fallback).

Behaviour comes from the app's source code (github.com/dockersamples/example-voting-app), not from its docs:
- vote/app.py: `redis.rpush` with socket_timeout=5 and no error handling. Redis gone = HTTP 500 at once,
  Redis paused = the request hangs, then 500 after 5 s.
- worker/Program.cs: one LPOP every ~100 ms (so at most ~10 units/s). Any Redis or Postgres exception escapes the
  loop and the process exits with code 1; a unit popped just before a failed INSERT is lost. On start it waits for
  Postgres, but exits if the name "redis" does not resolve.
- docker-compose.yml: no restart policy, so an exited container stays down. Postgres keeps data in a named volume;
  Redis keeps the queue in memory only.
These are hypotheses about the real stack until `python dev.py selftest` measures them on Docker.
"""
from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from dataclasses import dataclass, field

# Seconds from container start until the service answers (simulated warm-up).
WARMUP_S = {"vote": 1.5, "redis": 0.5, "db": 2.0, "worker": 1.0, "result": 1.0}
WORKER_TICK_S = 0.1  # Thread.Sleep(100)
WORKER_WORK_S = 0.003
REDIS_TIMEOUT_S = 5.0  # vote's socket_timeout and StackExchange.Redis' default sync timeout
PG_TIMEOUT_S = 30.0  # Npgsql default command timeout
SELF_HEAL = {"always", "unless-stopped", "on-failure"}


class Clock:
    """Simulated time. speed > 1 compresses it so tests run a 30 s experiment in about a second."""

    def __init__(self, speed: float = 1.0):
        self.speed = speed
        self._t0 = time.monotonic()

    def now(self) -> float:
        return (time.monotonic() - self._t0) * self.speed

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(max(0.0, seconds) / self.speed)


@dataclass
class Svc:
    name: str
    restart_policy: str | None = None
    state: str = "running"  # running | stopped | paused | restarting
    connected: bool = True
    cpus: float | None = None
    restarts: int = 0
    started_at: float = -100.0
    restart_at: float | None = None
    backoff: float = 0.1


@dataclass
class SimVoting:
    clock: Clock
    policies: dict[str, str | None] = field(default_factory=dict)
    seed: int = 7

    def __post_init__(self) -> None:
        self.svcs = {n: Svc(n, self.policies.get(n)) for n in WARMUP_S}
        self.queue: deque[tuple[str, str]] = deque()
        self.db: dict[str, str] = {}
        self.rng = random.Random(self.seed)
        self._t = self.clock.now()
        self._worker_next = self._t
        self._worker_ready = True
        self._worker_inflight: tuple[str, str] | None = None
        self._redis_hang: float | None = None
        self._db_hang: float | None = None

    # ---- state helpers -------------------------------------------------------------------------------------
    def reach(self, name: str, t: float) -> str:
        """'up', 'paused' or 'down' as seen from another container on the network."""
        s = self.svcs[name]
        if not s.connected or s.state in ("stopped", "restarting"):
            return "down"
        if s.state == "paused":
            return "paused"
        return "up" if t >= s.started_at + WARMUP_S[name] else "down"

    def _slow(self, name: str) -> float:
        cpus = self.svcs[name].cpus
        return 1.0 if not cpus else max(1.0, 1.0 / cpus)

    def _start(self, name: str, t: float) -> None:
        s = self.svcs[name]
        s.state, s.started_at, s.restart_at = "running", t, None
        if name == "worker":
            self._worker_ready, self._worker_next = False, t + WARMUP_S["worker"]
            self._redis_hang = self._db_hang = None

    def _exit(self, name: str, t: float) -> None:
        """The process died (crash, not a manual stop), so the restart policy applies."""
        s = self.svcs[name]
        if t - s.started_at > 10:
            s.backoff = 0.1  # Docker resets the restart delay after 10 s of uptime
        s.state = "stopped"
        if name == "redis":
            self.queue.clear()  # the queue lives in memory only
        if name == "worker":
            self._worker_inflight = None  # popped but never written: lost
        if s.restart_policy in SELF_HEAL:
            s.state, s.restart_at = "restarting", t + s.backoff
            s.backoff = min(s.backoff * 2, 8.0)

    # ---- time ----------------------------------------------------------------------------------------------
    def advance(self, t: float | None = None) -> None:
        t = self.clock.now() if t is None else t
        while True:
            restarts = [(s.restart_at, s.name) for s in self.svcs.values() if s.restart_at is not None]
            next_restart = min(restarts) if restarts else (float("inf"), "")
            worker_due = self._worker_next if self.svcs["worker"].state == "running" else float("inf")
            ev = min(next_restart[0], worker_due)
            if ev > t:
                break
            if next_restart[0] <= worker_due:
                self.svcs[next_restart[1]].restarts += 1
                self._start(next_restart[1], ev)
            else:
                self._worker_tick(ev)
        self._t = t

    def _worker_tick(self, t: float) -> None:
        nxt = t + (WORKER_TICK_S + WORKER_WORK_S) * self._slow("worker")
        if not self.svcs["worker"].connected:  # no DNS: OpenDbConnection keeps waiting, the loop throws
            if self._worker_ready:
                return self._exit("worker", t)
            self._worker_next = t + 1.0
            return
        if not self._worker_ready:  # Main(): OpenDbConnection (waits), then GetIp("redis") (throws if unresolvable)
            db, redis = self.reach("db", t), self.reach("redis", t)
            if db != "up":
                self._worker_next = t + 1.0
                return
            rs = self.svcs["redis"]
            if not (rs.connected and rs.state in ("running", "paused")):  # Dns.GetHostEntry throws, uncaught
                return self._exit("worker", t)
            if redis != "up":
                self._worker_next = t + 1.0
                return
            self._worker_ready = True
        redis = self.reach("redis", t)
        if redis == "down":
            return self._exit("worker", t)
        if redis == "paused":
            self._redis_hang = self._redis_hang if self._redis_hang is not None else t
            if t - self._redis_hang >= REDIS_TIMEOUT_S:
                return self._exit("worker", t)
            self._worker_next = t + WORKER_TICK_S
            return
        self._redis_hang = None
        if self._worker_inflight is None and self.queue:
            self._worker_inflight = self.queue.popleft()
        db = self.reach("db", t)
        if db == "down":
            return self._exit("worker", t)  # keep-alive or INSERT throws; an in-flight unit is lost
        if db == "paused":
            self._db_hang = self._db_hang if self._db_hang is not None else t
            if t - self._db_hang >= PG_TIMEOUT_S:
                return self._exit("worker", t)
            self._worker_next = t + WORKER_TICK_S
            return
        self._db_hang = None
        if self._worker_inflight is not None:
            vid, vote = self._worker_inflight
            self.db[vid] = vote
            self._worker_inflight = None
        self._worker_next = nxt

    # ---- what the probe sees -------------------------------------------------------------------------------
    def try_vote(self, voter_id: str, vote: str) -> tuple[str, int | None, float]:
        """One step of POST /. Returns ('done', status, latency_s) or ('wait', None, 0) while a dependency hangs."""
        t = self.clock.now()
        self.advance(t)
        v = self.reach("vote", t)
        if v == "down":
            return "done", None, 0.002  # connection refused
        if v == "paused":
            return "wait", None, 0.0
        base = (0.006 + self.rng.random() * 0.006) * self._slow("vote")
        r = self.reach("redis", t)
        if r == "down":
            return "done", 500, base
        if r == "paused":
            return "wait", None, 0.0
        self.queue.append((voter_id, vote))
        return "done", 200, base

    def stored_ids(self, prefix: str) -> set[str] | None:
        self.advance()
        if self.reach("db", self._t) != "up":
            return None
        return {k for k in self.db if k.startswith(prefix)}

    def queued_ids(self, prefix: str) -> set[str] | None:
        self.advance()
        if self.reach("redis", self._t) != "up":
            return None
        ids = {vid for vid, _ in self.queue if vid.startswith(prefix)}
        if self._worker_inflight and self._worker_inflight[0].startswith(prefix):
            ids.add(self._worker_inflight[0])
        return ids

    # ---- container actions ---------------------------------------------------------------------------------
    def crash(self, name: str) -> None:
        self.advance()
        if self.svcs[name].state in ("running", "paused"):
            self._exit(name, self._t)

    def stop(self, name: str) -> None:
        self.advance()
        self.svcs[name].state, self.svcs[name].restart_at = "stopped", None
        if name == "redis":
            self.queue.clear()

    def start(self, name: str) -> None:
        self.advance()
        if self.svcs[name].state in ("stopped", "restarting"):
            self._start(name, self._t)

    def pause(self, name: str) -> None:
        self.advance()
        if self.svcs[name].state == "running":
            self.svcs[name].state = "paused"

    def unpause(self, name: str) -> None:
        self.advance()
        if self.svcs[name].state == "paused":
            self.svcs[name].state = "running"

    def set_connected(self, name: str, connected: bool) -> None:
        self.advance()
        self.svcs[name].connected = connected

    def set_cpus(self, name: str, cpus: float | None) -> None:
        self.advance()
        self.svcs[name].cpus = cpus

    def is_up(self, name: str) -> bool:
        self.advance()
        return self.reach(name, self._t) == "up"
