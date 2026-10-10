"""The probe: sends units at a steady rate and classifies each one by business outcome, not by HTTP status alone.

Unit states (contract VoteState): pending (no answer yet), error (5xx or timeout), queued (acknowledged, not stored yet),
ok (acknowledged and found in the database), lost (acknowledged, then neither stored nor queued: silent data loss).
"""
from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass

SLA_S = 2.0  # a unit is healthy when it is stored within this many seconds of being sent
WINDOW_S = 3.0  # guard window for error rate and p95
POLL_S = 0.5


@dataclass
class Unit:
    n: int
    id: str
    sent: float
    done: float | None = None
    code: int | None = None
    latency_ms: float | None = None
    stored_at: float | None = None
    missing: int = 0
    lost: bool = False

    @property
    def acked(self) -> bool:
        return self.code is not None and 200 <= self.code < 300

    def state(self) -> str:
        if self.done is None:
            return "pending"
        if not self.acked:
            return "error"
        if self.stored_at is not None:
            return "ok"
        return "lost" if self.lost else "queued"

    def good(self) -> bool:
        return self.acked and self.stored_at is not None and self.stored_at - self.sent <= SLA_S

    def decided(self, now: float) -> bool:
        return self.done is not None and (not self.acked or self.stored_at is not None or self.lost
                                          or now - self.sent > SLA_S)


def p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)]


class Probe:
    def __init__(self, client, clock, rate: float, run_id: str, poll_s: float = POLL_S):
        self.poll_s = poll_s
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError("Probe rate must be a finite positive number")
        self.client, self.clock, self.rate = client, clock, rate
        self.prefix = f"cs-{run_id}-"
        self.units: list[Unit] = []
        self.sending = False
        self._tasks: set[asyncio.Task] = set()
        self._loops: list[asyncio.Task] = []
        self._monitored_tasks: list[asyncio.Task] = []
        self.db_visible = True

    def monitor(self, task: asyncio.Task) -> None:
        self._monitored_tasks.append(task)

    def ensure_healthy(self) -> None:
        """Never publish measurements as complete when polling or a scheduled fault silently died."""
        for task in [*self._loops, *self._monitored_tasks]:
            if task.done() and not task.cancelled() and task.exception() is not None:
                raise RuntimeError(f"Probe or scheduled fault failed: {task.exception()}") from task.exception()

    def start(self) -> None:
        self.sending = True
        self._loops = [asyncio.create_task(self._send_loop()), asyncio.create_task(self._poll_loop())]

    async def _send_loop(self) -> None:
        n, nxt = 0, self.clock.now()
        while self.sending:
            unit = Unit(n, f"{self.prefix}{n}", self.clock.now())
            self.units.append(unit)
            task = asyncio.create_task(self._one(unit))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            n += 1
            nxt += 1 / self.rate
            await self.clock.sleep(nxt - self.clock.now())

    async def _one(self, unit: Unit) -> None:
        try:
            code, latency = await self.client.send(unit.id)
        except Exception:  # a probe failure is an error unit, never a crash of the tool
            code, latency = None, (self.clock.now() - unit.sent) * 1000
        unit.code, unit.latency_ms, unit.done = code, latency, self.clock.now()

    async def _poll_loop(self) -> None:
        while True:
            await self.clock.sleep(self.poll_s)
            await self.poll()

    async def poll(self, final: bool = False) -> None:
        """Business check: which acknowledged units reached the database, which still sit in the queue."""
        if hasattr(self.client, "observed_ids"):
            queued, stored = await self.client.observed_ids(self.prefix)
        else:
            queued = await self.client.queued_ids(self.prefix)
            stored = await self.client.stored_ids(self.prefix)
        self.db_visible = stored is not None
        if stored is None:
            for u in self.units:
                u.missing = 0  # unavailable observations cannot establish consecutive absence
            return
        now = self.clock.now()
        for u in self.units:
            if not u.acked or u.stored_at is not None:
                continue
            if u.id in stored:
                u.stored_at, u.lost, u.missing = now, False, 0
            elif queued is not None and u.id not in queued and u.done is not None and now - u.done > 1.0:
                u.missing += 1  # two polls in a row rule out a unit caught between LPOP and INSERT
                u.lost = u.missing >= 2  # final polling must not weaken the evidence requirement
            else:
                u.missing = 0
                if queued is not None and u.id in queued:
                    u.lost = False

    async def stop_sending(self) -> None:
        self.sending = False

    async def drain(self, max_s: float) -> None:
        """Wait for in-flight requests to answer (they time out on their own)."""
        waited = 0.0
        while any(u.done is None for u in self.units) and waited < max_s:
            await self.clock.sleep(0.1)
            waited += 0.1

    async def close(self) -> None:
        self.sending = False
        for t in [*self._loops, *self._tasks]:
            t.cancel()
        await asyncio.gather(*self._loops, *self._tasks, return_exceptions=True)
        for u in self.units:  # anything still unanswered when the probe closes counts as a timeout
            if u.done is None:
                u.done, u.code = self.clock.now(), None
                u.latency_ms = max(0.0, u.done - u.sent) * 1000

    # ---- views -----------------------------------------------------------------------------------------------
    def fleet(self, size: int = 100) -> list[str]:
        return [u.state() for u in self.units[-size:]]

    def counters(self) -> dict:
        us = self.units
        return {"sent": len(us), "acked": sum(u.acked for u in us),
                "stored": sum(u.stored_at is not None for u in us), "lost": sum(u.lost for u in us),
                "errors": sum(u.done is not None and not u.acked for u in us)}

    def window(self, now: float, span: float = WINDOW_S) -> dict:
        done = [u for u in self.units if u.done is not None and now - span <= u.done <= now]
        errors = sum(not u.acked for u in done)
        # in-flight requests count with their current age, so a hang shows up before it times out
        latencies = [u.latency_ms or 0.0 for u in done] + [(now - u.sent) * 1000 for u in self.units if u.done is None]
        return {"completed": len(done), "errors": errors, "error_rate": errors / len(done) if done else 0.0,
                "p95_ms": p95(latencies)}

    def recovered_since(self, since: float, now: float, need: int = 5) -> bool:
        decided = [u for u in self.units if u.sent >= since and u.decided(now)]
        return len(decided) >= need and all(u.good() for u in decided[-need:])


def steady_check(window: dict, rate: float, baseline_p95_ms: float | None) -> tuple[bool, str]:
    """Is the app steady enough to inject a fault? Rate-aware: at low probe rates the window is longer and fewer
    samples are required (2 units/s cannot complete 5 in 2 s). Latency is judged against the measured baseline, so a
    slow-but-healthy app (hawkBit under emulation) is accepted while errors or a real slowdown are not."""
    need = max(3, math.ceil(rate * steady_span(rate) * 0.6))
    limit = max(1000.0, 3 * (baseline_p95_ms or 0))
    if window["completed"] < need:
        return False, f"only {window['completed']} of {need} needed answers"
    if window["error_rate"] > 0:
        return False, f"errors {window['error_rate']:.0%}"
    if window["p95_ms"] >= limit:
        return False, f"p95 {window['p95_ms']:.0f} ms over {limit:.0f} ms"
    return True, "steady"


def steady_span(rate: float) -> float:
    return max(2.0, 5.0 / rate)
