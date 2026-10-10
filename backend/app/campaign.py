"""The experiment engine: one experiment at a time, through the contract's phases, guard armed while the fault is live.

baseline -> inject -> observe -> restore -> recover -> settle -> done. Restore runs in `finally`, and the active fault is
persisted first, so even a crash of this process is undone on the next start.
"""
from __future__ import annotations

import asyncio
import secrets
import time

from . import agents, config, injectors, ledger, mapper, state
from .guard import Guard
from .probe import Probe, p95
from .runner import differs

LEAD_IN_S = 1.0  # normal traffic before the fault, so the chart shows the change
RECOVER_MAX_S = 30.0
SETTLE_S = 3.0


class Busy(RuntimeError):
    pass


class Engine:
    def __init__(self, target, runner, rate: float | None = None):
        self.target, self.runner = target, runner
        self.clock = runner.clock
        self.rate = rate or config.PROBE_RATE
        self.guard = Guard()
        self.phase = "idle"
        self.current: dict | None = None
        self.results: list[dict] = []
        self.baseline: dict | None = None
        self.error: str | None = None
        self.predictions: dict[str, dict] = {}
        self.task: asyncio.Task | None = None
        self._probe: Probe | None = None
        self._services: list[dict] = []
        self._cleanups: list = []
        self.refresh_graph()

    # ---- graph ---------------------------------------------------------------------------------------------
    def refresh_graph(self) -> None:
        self.graph = self.target.graph()
        self.sync = mapper.sync_path(self.graph, self.target.entry)
        self.order = mapper.topo_order(self.graph)
        self.catalog = injectors.catalog(self.graph, self.sync, self.target.entry, self.target.extra_experiments(self.graph))

    def experiment(self, exp_id: str) -> dict:
        for e in self.catalog:
            if e["id"] == exp_id:
                return e
        raise KeyError(exp_id)

    @property
    def busy(self) -> bool:
        return self.task is not None and not self.task.done()

    # ---- startup safety ------------------------------------------------------------------------------------
    async def recover_on_startup(self) -> list[str]:
        fault = state.load_active_fault()
        if not fault:
            return []
        restored = await self.runner.restore_all(fault["original"], self.order)
        state.clear_active_fault()
        self.error = f"Restored after an interrupted run ({fault.get('experiment_id')}): {', '.join(restored) or 'nothing to do'}"
        return restored

    # ---- baseline ------------------------------------------------------------------------------------------
    async def measure_baseline(self, duration_s: float = 5.0) -> dict:
        await self.target.prepare(self.runner)
        probe = Probe(self.runner.client(), self.clock, self.rate, f"b{secrets.token_hex(3)}")
        probe.start()
        try:
            await self.clock.sleep(duration_s)
            await probe.stop_sending()
            await probe.drain(6.0)
            await self.clock.sleep(1.0)
            await probe.poll(final=True)
        finally:
            await probe.close()
        done = [u for u in probe.units if u.done is not None]
        c = probe.counters()
        self.baseline = {"rps": round(len(done) / duration_s, 2),
                         "error_pct": round(100 * c["errors"] / max(1, c["sent"]), 1),
                         "p95_ms": round(p95([u.latency_ms or 0 for u in done]), 1),
                         "votes_sent": c["sent"], "votes_stored": c["stored"]}
        return self.baseline

    # ---- run -----------------------------------------------------------------------------------------------
    async def predict(self, exp_id: str) -> dict:
        exp = self.experiment(exp_id)
        pred = await agents.predict(exp, self.graph, entry=self.target.entry, sync=self.sync, rate=self.rate,
                                    fault_s=config.FAULT_S, history=self.results,
                                    unit_plural=self.target.unit["plural"], unit_singular=self.target.unit["singular"],
                                    baseline=self.baseline)
        self.predictions[exp_id] = pred
        return pred

    def start_run(self, exp_id: str, label: str = "before", fault_s: float | None = None) -> str:
        if self.busy:
            raise Busy("An experiment is already running")
        exp = self.experiment(exp_id)
        run_id = f"r{secrets.token_hex(3)}"
        self.error = None
        self.task = asyncio.create_task(self.run(run_id, exp, label, fault_s or config.FAULT_S))
        return run_id

    async def run(self, run_id: str, exp: dict, label: str, fault_s: float) -> dict | None:
        self.current = {"run_id": run_id, "experiment": exp, "prediction": self.predictions.pop(exp["id"], None),
                        "label": label, "elapsed_s": 0.0, "fault_duration_s": fault_s, "timeline": [], "fleet": [],
                        "counters": {"sent": 0, "acked": 0, "stored": 0, "lost": 0, "errors": 0}}
        started_wall = time.time()
        original: dict | None = None
        probe: Probe | None = None
        restored_at: float | None = None
        try:
            if self.baseline is None:
                self.phase = "baseline"
                await self.measure_baseline()
            original = await self.runner.snapshot()
            probe = self._probe = Probe(self.runner.client(), self.clock, self.rate, run_id)
            t_begin = self.clock.now()
            self.t0 = t_begin + LEAD_IN_S
            probe.start()
            self.phase = "baseline"  # a moment of normal traffic before the fault
            await self._watch(probe, until=self.t0, t_begin=t_begin, fault_s=fault_s)

            self.phase = "inject"
            state.save_active_fault({"run_id": run_id, "experiment_id": exp["id"], "original": original, "ts": time.time()})
            self.guard.arm()  # never a fault without the guard
            self._cleanups = await injectors.apply(self.runner, exp, probe)
            self.phase = "observe"
            await self._watch(probe, until=self.t0 + fault_s, t_begin=t_begin, fault_s=fault_s, guarded=True)

            self.phase = "restore"
            await self._run_cleanups()
            await self.runner.restore_all(original, self.order)
            state.clear_active_fault()
            restored_at = self.clock.now()
            self.guard.disarm()

            self.phase = "recover"
            while self.clock.now() - restored_at < RECOVER_MAX_S and self.clock.now() - t_begin < self.guard.thresholds.max_wall_s:
                if probe.recovered_since(restored_at, self.clock.now()):
                    break
                await self._tick(probe, t_begin, fault_s)
                await self.clock.sleep(0.5)

            self.phase = "settle"
            await probe.stop_sending()
            await probe.drain(6.0)
            end = self.clock.now() + SETTLE_S
            while self.clock.now() < end:
                await self._tick(probe, t_begin, fault_s)
                await self.clock.sleep(0.5)
            await probe.poll(final=True)
            await probe.close()
            await self._tick(probe, t_begin, fault_s)

            # final safety check: a service can fail after the fault is gone (e.g. on reconnect). Restart it and report it.
            notes: list[str] = []
            after = await self.runner.snapshot()
            broken = [n for n in self.order if n in original and differs(after[n], original[n])]
            if broken:
                await self.runner.restore_all(original, self.order)
                notes.append(f"Secondary failure: {', '.join(broken)} failed after the fault was removed and stayed down "
                             f"until Chaos Sentinel restarted it.")

            result = self._result(run_id, exp, label, probe, started_wall, restored_at)
            result["notes"] = notes
            self.results.append(result)
            self.phase = "done"
            return result
        except asyncio.CancelledError:
            self.error = "Stopped by the kill switch"
            self.phase = "done"
            raise
        except Exception as err:  # report, never leave a fault behind
            self.error = f"{type(err).__name__}: {err}"
            self.phase = "done"
            return None
        finally:
            await self._run_cleanups()
            if probe:
                await probe.close()
            if original is not None and restored_at is None:
                await asyncio.shield(self._restore(original))
            self.guard.disarm()
            self._probe = None
            state.save_snapshot(await self.state_json())

    async def _run_cleanups(self) -> None:
        cleanups, self._cleanups = self._cleanups, []
        for c in cleanups:
            try:
                await c()
            except Exception:  # a failed cleanup must not stop the restore
                pass

    async def _restore(self, original: dict) -> list[str]:
        restored = await self.runner.restore_all(original, self.order)
        state.clear_active_fault()
        return restored

    async def _watch(self, probe: Probe, *, until: float, t_begin: float, fault_s: float, guarded: bool = False) -> None:
        while self.clock.now() < until:
            await self.clock.sleep(0.5)
            await self._tick(probe, t_begin, fault_s)
            if guarded and self.guard.tripped:
                return

    async def _tick(self, probe: Probe, t_begin: float, fault_s: float) -> None:
        now = self.clock.now()
        self._services = await self.runner.services()
        unhealthy = sum(s["state"] != "running" or s["health"] == "unhealthy" for s in self._services)
        w = probe.window(now)
        self.guard.check(error_rate=w["error_rate"], completed=w["completed"], p95_ms=w["p95_ms"],
                         unhealthy=unhealthy, wall_s=now - t_begin)
        last = [u for u in probe.units if u.done is not None and now - 0.5 < u.done <= now]
        cur = self.current
        assert cur is not None
        cur["timeline"].append({"t": round(now - self.t0, 2), "ok": sum(u.acked for u in last),
                                "err": sum(not u.acked for u in last), "p95_ms": round(w["p95_ms"], 1),
                                "error_rate": round(w["error_rate"], 3), "unhealthy": unhealthy})
        cur["elapsed_s"] = round(now - self.t0, 1)
        cur["fleet"] = probe.fleet()
        cur["counters"] = probe.counters()

    def _result(self, run_id: str, exp: dict, label: str, probe: Probe, started_wall: float, restored_at: float) -> dict:
        units = probe.units
        c = probe.counters()
        done = [u for u in units if u.done is not None]
        timeline = self.current["timeline"] if self.current else []
        # recovery: from the first unhealthy unit after the fault to the start of the final unbroken healthy run (>= 5)
        after = [u for u in units if u.sent >= self.t0]
        first_bad = next((i for i, u in enumerate(after) if not u.good()), None)
        recovery: float | None
        if first_bad is None:
            recovery = 0.0
        else:
            streak_start = len(after)
            while streak_start > 0 and after[streak_start - 1].good():
                streak_start -= 1
            ok_streak = len(after) - streak_start >= 5 and streak_start > first_bad
            recovery = round(after[streak_start].sent - after[first_bad].sent, 1) if ok_streak else None
        outcome = {**c, "error_pct": round(100 * c["errors"] / max(1, c["sent"]), 1),
                   "p95_ms": round(p95([u.latency_ms or 0 for u in done]), 1),
                   "peak_p95_ms": round(max((p["p95_ms"] for p in timeline), default=0.0), 1),
                   "recovery_s": recovery, "availability_pct": round(100 * c["acked"] / max(1, c["sent"]), 1),
                   "guard_tripped": self.guard.tripped, "guard_reason": self.guard.reason, "data_loss": c["lost"] > 0}
        pred = self.current["prediction"] if self.current else None
        perr = None
        if pred:
            perr = {"errors_pct_points": round(outcome["error_pct"] - pred["expected_error_pct"], 1),
                    "votes_lost": outcome["lost"] - pred["expected_votes_lost"],
                    "recovery_s": round((recovery if recovery is not None else RECOVER_MAX_S) - pred["expected_recovery_s"], 1)}
        return {"run_id": run_id, "experiment": exp, "prediction": pred, "outcome": outcome, "timeline": timeline,
                "fleet": probe.fleet(), "started_at": started_wall, "duration_s": round(time.time() - started_wall, 1),
                "label": label, "prediction_error": perr}

    # ---- kill switch ---------------------------------------------------------------------------------------
    async def killswitch(self) -> list[str]:
        """Stop whatever is running and put every service back the way it was before the fault."""
        fault = state.load_active_fault()
        want = fault["original"] if fault else \
            {s: {"state": "running", "connected": True, "cpus": None} for s in self.order}
        before = await self.runner.snapshot()
        if self.busy and self.task:
            self.task.cancel()  # the run restores in its finally block
            await asyncio.gather(self.task, return_exceptions=True)
        await self._restore(want)
        return [n for n in self.order if n in before and n in want and differs(before[n], want[n])]

    # ---- state ---------------------------------------------------------------------------------------------
    async def state_json(self) -> dict:
        ready, detail = await self.runner.ready()
        if not self.busy:
            self._services = await self.runner.services()
        cur = None
        if self.current:
            seconds_left = 0.0
            if self.phase == "observe" and self.guard.armed:
                seconds_left = self.current["fault_duration_s"] - self.current["elapsed_s"]
            cur = {**self.current, "guard": self.guard.view(seconds_left)}
        return {
            "runner": {"kind": self.runner.kind, "ready": ready, "detail": detail},
            "services": self._services,
            "baseline": {k: self.baseline[k] for k in ("rps", "error_pct", "p95_ms")} if self.baseline else None,
            "phase": self.phase,
            "current": cur,
            "results": self.results,
            "campaign": {"status": "idle", "strategy": "ai", "budget": 0, "picks": [], "remaining": []},
            "analysis": None,
            "fixes": None,
            "applied_fixes": [],
            "compare": None,
            "cost": ledger.summary(),
            "error": self.error,
        }
