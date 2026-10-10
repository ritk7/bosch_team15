"""The experiment engine: one experiment at a time, through the contract's phases, guard armed while the fault is live.

baseline -> inject -> observe -> restore -> recover -> settle -> done. Restore runs in `finally`, and the active fault is
persisted first, so even a crash of this process is undone on the next start.
"""
from __future__ import annotations

import asyncio
import math
import secrets
import time
from contextlib import asynccontextmanager
import yaml

from . import agents, analysis, config, fixes, injectors, ledger, mapper, planner, score, state
from .guard import Guard, Thresholds
from .probe import Probe, p95, steady_check, steady_span
from .runner import differs

LEAD_IN_S = 1.0  # normal traffic before the fault, so the chart shows the change
RECOVER_MAX_S = 30.0
SETTLE_S = 3.0


class Busy(RuntimeError):
    pass


class Engine:
    def __init__(self, target, runner, rate: float | None = None, persist: bool = True):
        self.persist = persist  # the twin engine never touches the safety files
        self.target, self.runner = target, runner
        self.clock = runner.clock
        if rate is None:
            rate = config.PROBE_RATE if config.PROBE_RATE_SET else getattr(target, "probe_rate", config.PROBE_RATE)
        self.rate = rate
        if not math.isfinite(self.rate) or self.rate <= 0:
            raise ValueError("Probe rate must be a finite positive number")
        self.guard = Guard()  # thresholds set in refresh_graph (blast radius depends on the app's size)
        self.phase = "idle"
        self.current: dict | None = None
        self.results: list[dict] = []
        self.baseline: dict | None = None
        self.error: str | None = None
        self.predictions: dict[str, dict] = {}
        self.task: asyncio.Task | None = None
        self._operation_task: asyncio.Task | None = None
        self._probe: Probe | None = None
        self._services: list[dict] = []
        self._cleanups: list = []
        self._targets: set[str] = set()
        self._status: dict | None = None
        self._status_task: asyncio.Task | None = None
        self.restored_from: float | None = None
        self._expected: dict | None = None  # service states at the first run: the healthy app
        self.analysis: dict | None = None
        self.fixes: dict | None = None
        self.applied_fixes: list[str] = []
        self.score_history: list[dict] = []
        self.campaign = {"status": "idle", "strategy": "ai", "budget": 0, "picks": [], "remaining": []}
        self.compare: dict | None = None
        self.twin: dict | None = None
        self.plan: dict | None = None
        self.refresh_graph()

    # ---- graph ---------------------------------------------------------------------------------------------
    def refresh_graph(self) -> None:
        self.graph = self.target.graph()
        self.sync = mapper.sync_path(self.graph, self.target.entry)
        self.order = mapper.topo_order(self.graph)
        self.catalog = injectors.catalog(self.graph, self.sync, self.target.entry, self.target.extra_experiments(self.graph),
                                         db_load=(self.target.db_load or {}).get("service"))
        # blast radius: collateral services (not faulted on purpose). Small apps get a tighter limit.
        n = len(self.graph["nodes"])
        self.guard.base = self.guard.thresholds = Thresholds(unhealthy=2 if n >= 4 else 1)

    def experiment(self, exp_id: str) -> dict:
        for e in self.catalog:
            if e["id"] == exp_id:
                return e
        raise KeyError(exp_id)

    @property
    def busy(self) -> bool:
        return (self.task is not None and not self.task.done()) or self._operation_task is not None

    @asynccontextmanager
    async def operation(self):
        """Reserve the engine across HTTP awaits, not just background experiments."""
        if self.busy:
            raise Busy("Another operation is already running")
        self._operation_task = asyncio.current_task()
        try:
            yield
        finally:
            self._operation_task = None

    def _check_fault_scope(self, fault: dict) -> None:
        if (fault.get("target", self.target.name) != self.target.name
                or fault.get("runner", self.runner.kind) != self.runner.kind
                or fault.get("project", getattr(self.runner, "project", None)) != getattr(self.runner, "project", None)
                or not set(fault.get("original", {})).issubset(self.order)):
            raise RuntimeError("Saved fault belongs to another target/runner. Recovery record preserved; start that target to restore it.")

    # ---- startup safety ------------------------------------------------------------------------------------
    async def recover_on_startup(self) -> list[str]:
        if not self.persist:
            return []
        fault = state.load_active_fault()
        if not fault:
            return []
        self._check_fault_scope(fault)
        restored = await self.runner.restore_all(fault["original"], self.order)
        state.clear_active_fault()
        self.error = f"Restored after an interrupted run ({fault.get('experiment_id')}): {', '.join(restored) or 'nothing to do'}"
        return restored

    def load_snapshot(self, snap: dict | None) -> bool:
        """After a restart of the tool, bring back runs, findings and score (same target and runner only)."""
        if not snap or snap.get("target") != self.target.name or (snap.get("runner") or {}).get("kind") != self.runner.kind:
            return False
        # never pass old runs off as live: the UI shows when they were saved until the demo is reset or a new run starts
        # only label restored runs if there are any (an empty saved session is not "old data")
        self.restored_from = (snap.get("saved_at") or snap["results"][-1].get("started_at")) if snap.get("results") else None
        self.results = snap.get("results") or []
        self.analysis, self.fixes = snap.get("analysis"), snap.get("fixes")
        self.applied_fixes = snap.get("applied_fixes") or []
        if self.fixes:
            # Public snapshots omit executor metadata. Rebuild legacy restart targets from validated rule snippets,
            # preserving fix IDs so approval/applied status never shifts to a different recommendation.
            found = (self.analysis or {}).get("findings", [])
            refreshed = {x["title"]: x for x in analysis.rules_fixes(found, self.results, self.graph)}
            restored_fixes = []
            for saved in self.fixes.get("fixes", []):
                fx = dict(saved)
                if fx.get("auto_applicable"):
                    try:
                        services = (yaml.safe_load(fx["snippet"]) or {}).get("services", {})
                        if (not isinstance(services, dict) or not services
                                or any(s not in self.order or not isinstance(spec, dict)
                                       or set(spec) != {"restart"} or spec["restart"] != "unless-stopped"
                                       for s, spec in services.items())):
                            raise ValueError("Invalid saved restart fix")
                        fx["_services"] = sorted(services)
                        fx["_findings"] = [f["id"] for f in found if f["service"] in services] or [fx["finding_id"]]
                    except (ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError):
                        fx["auto_applicable"] = False
                elif fx["title"] in refreshed:
                    fresh = refreshed[fx["title"]]
                    fx = {**fresh, "id": fx["id"], "finding_id": fx["finding_id"]}
                restored_fixes.append(fx)
            self.fixes = {"fixes": restored_fixes, "decided_by": "rules", "model": "rules engine (revalidated saved fixes)"}
        self.score_history = snap.get("score_history") or []
        self.compare, self.twin, self.plan = snap.get("compare"), snap.get("twin"), snap.get("plan")
        camp = snap.get("campaign") or {}
        if camp.get("status") == "running":
            camp["status"] = "stopped"
        self.campaign = camp or self.campaign
        # A saved baseline is historical evidence, not proof the app is healthy after a restart.
        self.baseline = None
        if self.results:
            last = self.results[-1]
            self.current = {"run_id": last["run_id"], "experiment": last["experiment"], "prediction": last["prediction"],
                            "label": last["label"], "elapsed_s": last["timeline"][-1]["t"] if last["timeline"] else 0,
                            "wall_s": last.get("wall_s"),
                            "fault_duration_s": last.get("fault_duration_s", config.FAULT_S),
                            "fault_ended_s": last.get("fault_ended_s"), "timeline": last["timeline"], "fleet": last["fleet"],
                            "counters": {k: last["outcome"][k] for k in ("sent", "acked", "stored", "lost", "errors")}}
            self.guard.tripped = last["outcome"]["guard_tripped"]
            self.guard.reason = last["outcome"]["guard_reason"]
            self.phase = "done"
        return True

    def save(self) -> None:
        """Snapshot after analysis/fix changes too, not only after runs."""
        if self.persist:
            asyncio.get_event_loop().create_task(self._save())

    async def _save(self) -> None:
        state.save_snapshot(await self.state_json())

    # ---- baseline ------------------------------------------------------------------------------------------
    async def measure_baseline(self, duration_s: float = 5.0, _warm_retry: bool = True) -> dict:
        if not math.isfinite(duration_s) or not 1 <= duration_s <= 60:
            raise ValueError("Baseline duration must be between 1 and 60 seconds")
        await self.target.prepare(self.runner)
        probe = Probe(self.runner.client(), self.clock, self.rate, f"b{secrets.token_hex(3)}")
        probe.start()
        try:
            await self.clock.sleep(duration_s)
            await probe.stop_sending()
            await probe.drain(6.0)
            await self.clock.sleep(1.0)
            probe.ensure_healthy()
            await probe.poll(final=True)
        finally:
            await probe.close()
        done = [u for u in probe.units if u.done is not None]
        c = probe.counters()
        svcs = await self.runner.services()
        healthy = sum(s["state"] == "running" and s["health"] != "unhealthy" for s in svcs)
        self.baseline = {"rps": round(len(done) / duration_s, 2), "healthy_services": healthy, "measured_at": time.time(),
                         "total_services": len(svcs),
                         "error_pct": round(100 * c["errors"] / max(1, c["sent"]), 1),
                         "p95_ms": round(p95([u.latency_ms or 0 for u in done]), 1),
                         "votes_sent": c["sent"], "votes_stored": c["stored"]}
        if _warm_retry and self.baseline["p95_ms"] > 1000:
            # measured: a cold hawkBit gave p95 2288 ms vs 163 ms warm, which would loosen the latency guard
            await self.clock.sleep(10.0)
            return await self.measure_baseline(duration_s, _warm_retry=False)
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
        if self.persist and state.load_active_fault():
            raise Busy("A previous fault needs restoration; press the kill switch first")
        exp = self.experiment(exp_id)
        self.restored_from = None
        run_id = f"r{secrets.token_hex(3)}"
        self.error = None
        if not fault_s:  # how safely: the planner's window, else shorter for riskier experiments
            pred = self.predictions.get(exp_id) or {}
            fault_s = pred.get("recommended_fault_s") or injectors.recommended_fault_s(exp)
        self.task = asyncio.create_task(self.run(run_id, exp, label, fault_s))
        return run_id

    async def run(self, run_id: str, exp: dict, label: str, fault_s: float) -> dict | None:
        self.guard = Guard(self.guard.thresholds)  # a previous run's trip must not leak into the next baseline
        self.current = {"run_id": run_id, "experiment": exp, "prediction": self.predictions.pop(exp["id"], None),
                        "label": label, "elapsed_s": 0.0, "wall_s": 0.0, "fault_duration_s": fault_s, "fault_ended_s": None, "timeline": [], "fleet": [],
                        "counters": {"sent": 0, "acked": 0, "stored": 0, "lost": 0, "errors": 0}}
        started_wall = time.time()
        original: dict | None = None
        probe: Probe | None = None
        restored_at: float | None = None
        try:
            self._enter("baseline")
            if self._expected is not None:
                before = await self.runner.snapshot()
                changed = [n for n in self.order if n in self._expected and differs(before[n], self._expected[n])]
                if changed:
                    await self.runner.restore_all(self._expected, self.order)
                    self.baseline = None
                    self.error = f"Pre-flight restored {', '.join(changed)}, which differed from the healthy app"
            await self.runner.wait_ready(self.order)
            if not await self._app_ready_within(60.0):  # running containers are not enough: the app must answer
                ok, why = await self.target.app_ready(self.runner)
                raise RuntimeError(f"The application is not answering ({why}); nothing was injected")
            if self.baseline and time.time() - self.baseline.get("measured_at", time.time()) > 600:
                self.baseline = None  # older than 10 minutes: re-measure
            def unhealthy(b: dict) -> bool:
                return not b["votes_sent"] or b["error_pct"] > 0 or b["votes_stored"] != b["votes_sent"]

            measured_now = self.baseline is None
            if measured_now:
                await self.measure_baseline()
            # a backlog from the previous experiment (e.g. 200 queued votes after the ghost-votes run) can still be
            # draining: no errors, just units not stored yet. Give it up to ~1 minute; errors get a single retry.
            tries = 0
            while measured_now and unhealthy(self.baseline) and tries < (4 if self.baseline["error_pct"] == 0 else 1):
                tries += 1
                await self.clock.sleep(15.0)
                await self.measure_baseline()
            if unhealthy(self.baseline):
                self.baseline = None  # never keep an unhealthy baseline: the next run measures again
                raise RuntimeError("Baseline is not healthy: all probe units must be acknowledged and stored before injecting a fault")
            original = await self.runner.snapshot()
            if self._expected is None:
                self._expected = original
            probe = self._probe = Probe(self.runner.client(), self.clock, self.rate, run_id, poll_s=self._poll_s())
            t_begin = self.clock.now()
            self.t0 = t_begin + LEAD_IN_S
            probe.start()
            self._enter("baseline")  # when: only inject into a steady app
            await self._watch(probe, until=self.t0, t_begin=t_begin, fault_s=fault_s)
            span = steady_span(self.rate)
            steady_by = self.clock.now() + max(10.0, 3 * span)
            while True:
                w = probe.window(self.clock.now(), span=span)
                ok, why = steady_check(w, self.rate, (self.baseline or {}).get("p95_ms"))
                if ok:
                    break
                if self.clock.now() > steady_by:
                    raise RuntimeError(f"App not steady before the fault ({why}); nothing was injected")
                await self._tick(probe, t_begin, fault_s)
                await self.clock.sleep(0.5)
            self.t0 = self.clock.now()

            self._enter("inject")
            if self.persist:
                state.save_active_fault({"run_id": run_id, "experiment_id": exp["id"], "original": original, "ts": time.time(),
                                         "target": self.target.name, "runner": self.runner.kind,
                                         "project": getattr(self.runner, "project", None)})
            self.guard.arm(self.baseline["p95_ms"] if self.baseline else None)  # never a fault without the guard
            self._targets = injectors.targets_of(exp)
            self._cleanups = await injectors.apply(self.runner, exp, probe)
            self._enter("observe")
            await self._watch(probe, until=self.t0 + fault_s, t_begin=t_begin, fault_s=fault_s, guarded=True)

            self._enter("restore")
            await self._run_cleanups()
            await self.runner.restore_all(original, self.order)
            if self.persist:
                state.clear_active_fault()
            restored_at = self.clock.now()
            self.current["fault_ended_s"] = round(restored_at - self.t0, 2)
            self.guard.disarm()

            self._enter("recover")
            # The guard's wall limit bounds an armed fault, not observation after verified restoration. Slow
            # health checks must not consume the separate, bounded recovery window and hide actual recovery.
            while self.clock.now() - restored_at < RECOVER_MAX_S:
                if probe.recovered_since(restored_at, self.clock.now()):
                    break
                await self._tick(probe, t_begin, fault_s)
                await self.clock.sleep(0.5)

            self._enter("settle")
            await probe.stop_sending()
            await probe.drain(6.0)
            end = self.clock.now() + SETTLE_S
            while self.clock.now() < end:
                await self._tick(probe, t_begin, fault_s)
                await self.clock.sleep(0.5)
            await probe.poll(final=True)
            await probe.close()
            await self._tick(probe, t_begin, fault_s)

            # final safety check: a service can fail after the fault is gone (e.g. on reconnect). Watch a few seconds more,
            # then restart anything that differs from before the fault, and report it.
            notes: list[str] = []
            broken: list[str] = []
            for _ in range(10):
                after = await self.runner.snapshot()
                broken = [n for n in self.order if n in original and differs(after[n], original[n])]
                if broken:
                    break
                await self.clock.sleep(0.5)
            if broken:
                await self.runner.restore_all(original, self.order)
                notes.append(f"Secondary failure: {', '.join(broken)} failed after the fault was removed and stayed down "
                             f"until Chaos Sentinel restarted it.")
            # containers running is not enough: the application itself must answer again
            if not await self._app_ready_within(30.0):
                entry = self.target.entry
                await self.runner.restart(entry)
                ok = await self._app_ready_within(120.0)
                notes.append(f"Secondary failure: the {entry} application stayed unresponsive after the fault was removed "
                             f"(its containers were running); Chaos Sentinel restarted {entry}"
                             + ("." if ok else ", and it is still not answering."))
            if notes:
                self.baseline = None  # the app was disrupted beyond the experiment: measure a fresh baseline next time

            result = self._result(run_id, exp, label, probe, started_wall, restored_at)
            result["notes"] = notes
            self.results.append(result)
            if self.persist:  # live findings and score after every run; "Analyse" rewrites them with the AI
                self.analysis = {"findings": analysis.rules_findings(self.results, self.graph, self.target.unit,
                                                                     self.runner.kind),
                                 "score": score.compute(self.results, self.graph), "decided_by": "rules",
                                 "model": "rules engine (live)"}
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
                try:
                    await asyncio.shield(self._restore(original))
                    if probe:
                        self.current["fault_ended_s"] = round(self.clock.now() - self.t0, 2)
                except Exception as err:
                    self.error = f"{self.error or 'Run failed'}; restoration failed: {err}. Recovery record retained."
            self.guard.disarm()
            self._probe = None
            if self.persist:
                state.save_snapshot(await self.state_json())

    async def _run_cleanups(self) -> None:
        cleanups, self._cleanups = self._cleanups, []
        for c in cleanups:
            try:
                await c()
            except Exception:  # a failed cleanup must not stop the restore
                pass

    async def _app_ready_within(self, max_s: float) -> bool:
        waited = 0.0
        while True:
            ok, _ = await self.target.app_ready(self.runner)
            if ok or waited >= max_s:
                return ok
            await self.clock.sleep(2.0)
            waited += 2.0

    def _poll_s(self) -> float:
        return 1.0 if self.runner.kind == "docker" else 0.5

    # ---- status cache: Docker CLI calls take ~300 ms; never make the UI or the guard loop wait on them ------------
    def _ensure_status_loop(self) -> None:
        if self.runner.kind != "docker" or (self._status_task and not self._status_task.done()):
            return

        async def loop() -> None:
            while True:
                try:
                    ready, detail = await self.runner.ready()
                    svcs = await self.runner.services()
                    self._status = {"ready": ready, "detail": detail, "services": svcs, "at": time.time()}
                except Exception as err:  # Docker hiccup: keep the last view, say why
                    if self._status:
                        self._status["detail"] = f"status refresh failed: {err}"
                await asyncio.sleep(1.0)

        self._status_task = asyncio.create_task(loop())

    async def _current_status(self) -> dict:
        if self.runner.kind != "docker":
            ready, detail = await self.runner.ready()
            return {"ready": ready, "detail": detail, "services": await self.runner.services(), "at": time.time()}
        self._ensure_status_loop()
        if self._status is None or time.time() - self._status["at"] > 5:
            ready, detail = await self.runner.ready()
            self._status = {"ready": ready, "detail": detail, "services": await self.runner.services(), "at": time.time()}
        return self._status

    def _enter(self, phase: str) -> None:
        """Move to a phase and remember it, so the UI never shows phases that did not run (e.g. after a refused start)."""
        self.phase = phase
        if self.current is not None and phase not in self.current.setdefault("phases", []):
            self.current["phases"].append(phase)

    async def _restore(self, original: dict) -> list[str]:
        restored = await self.runner.restore_all(original, self.order)
        if self.persist:
            state.clear_active_fault()
        return restored

    async def _watch(self, probe: Probe, *, until: float, t_begin: float, fault_s: float, guarded: bool = False) -> None:
        while self.clock.now() < until:
            await self.clock.sleep(0.5)
            await self._tick(probe, t_begin, fault_s)
            if guarded and self.guard.tripped:
                return

    async def _tick(self, probe: Probe, t_begin: float, fault_s: float) -> None:
        probe.ensure_healthy()
        self._services = (await self._current_status())["services"]
        now = self.clock.now()  # Docker inspection itself can take time; never judge a stale wall clock
        # blast radius = services down or unhealthy that were not faulted on purpose
        on_purpose = self._targets if self.guard.armed else set()
        unhealthy = sum((s["state"] != "running" or s["health"] == "unhealthy") and s["id"] not in on_purpose
                        for s in self._services)
        w = probe.window(now)
        self.guard.check(error_rate=w["error_rate"], completed=w["completed"], p95_ms=w["p95_ms"],
                         unhealthy=unhealthy, wall_s=now - t_begin)
        last = [u for u in probe.units if u.done is not None and now - 0.5 < u.done <= now]
        cur = self.current
        assert cur is not None
        t_rel = round(now - self.t0, 2)
        if cur["timeline"] and t_rel - cur["timeline"][-1]["t"] < 0.2:
            cur["timeline"].pop()  # two loops can tick at the same instant (phase change): keep one point per moment
        cur["timeline"].append({"t": t_rel, "ok": sum(u.acked for u in last),
                                "err": sum(not u.acked for u in last), "p95_ms": round(w["p95_ms"], 1),
                                "error_rate": round(w["error_rate"], 3), "unhealthy": unhealthy})
        cur["elapsed_s"] = round(now - self.t0, 1)
        cur["wall_s"] = round(now - t_begin, 1)
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
            recovery = 0.0 if len(after) >= 5 else None
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
                    "recovery_s": round(recovery - pred["expected_recovery_s"], 1) if recovery is not None else None}
        return {"run_id": run_id, "experiment": exp, "prediction": pred, "outcome": outcome, "timeline": timeline,
                "fleet": probe.fleet(), "started_at": started_wall, "duration_s": round(time.time() - started_wall, 1),
                "label": label, "prediction_error": perr,
                "configuration_signature": agents.configuration_signature(self.graph, self.rate, self.current["fault_duration_s"]),
                "wall_s": self.current["wall_s"],
                "fault_duration_s": self.current["fault_duration_s"], "fault_ended_s": self.current["fault_ended_s"]}

    # ---- analysis, fixes, re-run ---------------------------------------------------------------------------
    async def analyze(self) -> dict:
        found, source, model = await analysis.findings(self.results, self.graph, self.target.unit, self.runner.kind)
        sc = score.compute(self.results, self.graph)
        self.analysis = {"findings": found, "score": sc, "decided_by": source, "model": model}
        label = "after fix" if self.applied_fixes else "before fix"
        if self.score_history and self.score_history[-1]["label"] == label:
            self.score_history[-1] = {"label": label, "value": sc["value"], "ts": time.time()}  # same stage: latest wins
        else:
            self.score_history.append({"label": label, "value": sc["value"], "ts": time.time()})
        self.save()
        return self.analysis

    async def recommend(self) -> dict:
        if self.analysis is None:
            await self.analyze()
        assert self.analysis is not None
        fx, source, model = await analysis.recommend(self.analysis["findings"], self.results, self.graph)
        self.fixes = {"fixes": fx, "decided_by": source, "model": model}
        self.save()
        return self.fixes

    async def apply_fixes(self, fix_ids: list[str]) -> dict:
        """Engineer-approved: write the override and recreate only the services it changes."""
        if self.busy and self._operation_task is not asyncio.current_task():
            raise Busy("An experiment is running")
        chosen = [x for x in (self.fixes or {}).get("fixes", []) if x["id"] in fix_ids]
        if not chosen:
            raise ValueError("No such fixes. Run /api/fixes first.")
        advice = [x["id"] for x in chosen if not x["auto_applicable"]]
        if advice:
            raise ValueError(f"{', '.join(advice)} are advice only and cannot be applied automatically")
        restart = {svc: "unless-stopped" for x in chosen for svc in x.get("_services", [])}
        if not restart or any(not x.get("_services") for x in chosen):
            raise ValueError("Saved fixes have no executable service targets. Regenerate recommendations before applying.")
        overlay = fixes.write_overlay(self.target.overlay_file(), restart, {n["id"] for n in self.graph["nodes"]})
        self.refresh_graph()
        await self.runner.apply_overlay(sorted(restart))
        self.baseline = None
        self.applied_fixes = sorted(set(self.applied_fixes) | {x["id"] for x in chosen})
        self.save()
        return {"ok": True, "overlay": overlay,
                "detail": f"restart: unless-stopped on {', '.join(sorted(restart))}; re-run the experiments to prove it"}

    def start_rerun(self, exp_ids: list[str] | None = None) -> list[str]:
        if self.busy:
            raise Busy("An experiment is running")
        if not exp_ids:
            fx = [x for x in (self.fixes or {}).get("fixes", []) if x["id"] in self.applied_fixes]
            fids = {fid for x in fx for fid in x.get("_findings", [x["finding_id"]])}
            exp_ids = [f["experiment_id"] for f in (self.analysis or {}).get("findings", []) if f["id"] in fids] or \
                [r["experiment"]["id"] for r in score.latest(self.results)]
        exps = [self.experiment(i) for i in dict.fromkeys(exp_ids)]

        async def sequence() -> None:
            for exp in exps:
                await self.predict(exp["id"])
                if await self.run(f"r{secrets.token_hex(3)}", exp, "after fix", config.FAULT_S) is None:
                    return
            await self.analyze()

        self.task = asyncio.create_task(sequence())
        return [e["id"] for e in exps]

    # ---- campaign and twin -----------------------------------------------------------------------------------
    def start_campaign(self, budget: int, strategy: str, seed: int = 7) -> None:
        if self.busy:
            raise Busy("An experiment is running")
        budget = max(1, min(budget, len(self.catalog)))
        self.campaign = {"status": "running", "strategy": strategy, "budget": budget, "picks": [],
                         "remaining": [e["id"] for e in self.catalog]}

        async def loop() -> None:
            done: list[dict] = []
            order = planner.random_order(self.catalog, seed) if strategy == "random" else []
            try:
                for i in range(budget):
                    remaining = [e for e in self.catalog if e["id"] in self.campaign["remaining"]]
                    if strategy == "random":
                        p = {"experiment": order[i], "reason": f"random order (seed {seed})",
                             "hypothesis": order[i]["description"], "decided_by": "rules", "model": "random"}
                    else:
                        p = await planner.pick(remaining, done, self.graph, self.twin_penalties())
                    exp = p["experiment"]
                    self.campaign["picks"].append({"experiment_id": exp["id"], "reason": p["reason"],
                                                   "hypothesis": p["hypothesis"], "decided_by": p["decided_by"],
                                                   "model": p["model"], "order": i + 1})
                    self.campaign["remaining"] = [x for x in self.campaign["remaining"] if x != exp["id"]]
                    await self.predict(exp["id"])
                    r = await self.run(f"r{secrets.token_hex(3)}", exp, f"campaign {strategy}", config.FAULT_S)
                    if r:
                        done.append(r)
                    else:
                        self.campaign["status"] = "stopped"
                        return  # do not inject another fault after a failed or unrestored run
                self.campaign["status"] = "done"
                await self.analyze()
                await self.recommend()
                await self.run_twin()
            except asyncio.CancelledError:
                self.campaign["status"] = "stopped"
                raise
            except Exception as err:
                self.campaign["status"] = "stopped"
                self.error = f"Campaign failed: {type(err).__name__}: {err}"
            finally:
                if self.persist:
                    state.save_snapshot(await self.state_json())

        self.task = asyncio.create_task(loop())

    async def stop_campaign(self) -> list[str]:
        restored = await self.killswitch()
        if self.campaign["status"] == "running":
            self.campaign["status"] = "stopped"
        return restored

    async def run_twin(self, speed: float = 60.0) -> dict:
        """Rehearse the whole catalog on the digital twin (simulation with the same graph and restart policies)."""
        from .runner import SimRunner
        from .sim import Clock

        self.twin = {"status": "running", "rows": [], "ran_at": time.time()}
        try:
            return await self._run_twin(speed)
        finally:
            if self.twin.get("status") == "running":
                self.twin["status"] = "done"
                self.twin["failed"] = self.twin.get("failed") or [{"experiment_id": "*", "title": "rehearsal",
                                                                   "reason": self.error or "interrupted"}]

    async def _run_twin(self, speed: float) -> dict:
        from .runner import SimRunner
        from .sim import Clock

        twin = Engine(self.target, SimRunner(self.target, Clock(speed)), rate=self.rate, persist=False)
        # the twin measures its own baseline, so the same "healthy baseline before any fault" gate applies to it
        started = time.perf_counter()
        fan = {n["id"]: n["fan_in"] for n in self.graph["nodes"]}
        self.twin["attempted"], self.twin["failed"] = len(twin.catalog), []
        for exp in twin.catalog:
            r = await twin.run(f"t{secrets.token_hex(2)}", exp, "twin", config.FAULT_S)
            if not r:  # never hide a skipped rehearsal: report it with its reason
                self.twin["failed"].append({"experiment_id": exp["id"], "title": exp["title"], "reason": twin.error})
                continue
            o = r["outcome"]
            self.twin["rows"].append({"experiment_id": exp["id"], "title": exp["title"], "target": exp["target"],
                                      "type": exp["type"], "penalty": round(score.penalty(r, fan.get(exp["target"], 0))[0], 1),
                                      "error_pct": o["error_pct"], "lost": o["lost"], "recovery_s": o["recovery_s"],
                                      "guard_tripped": o["guard_tripped"]})
        self.twin["status"] = "done"
        self.twin["completed"] = len(self.twin["rows"])
        self.twin["seconds"] = round(time.perf_counter() - started, 1)
        if self.twin["failed"]:
            self.compare = {"planner_runs_to_top3": None, "random_runs_to_top3": None,
                            "note": f"Not computed: only {self.twin['completed']} of {self.twin['attempted']} rehearsals "
                                    f"completed, so the comparison would be biased."}
        else:
            self.compare = planner.compare(twin.results, twin.catalog, self.graph)
        self.save()
        return self.twin

    def twin_penalties(self) -> dict[str, float] | None:
        rows = (self.twin or {}).get("rows") if (self.twin or {}).get("status") == "done" else None
        return {r["experiment_id"]: r["penalty"] for r in rows} if rows else None

    async def make_plan(self) -> dict:
        self.plan = await planner.rank_all(self.catalog, self.results, self.graph, self.twin_penalties())
        self.save()
        return self.plan

    def start_twin(self) -> None:
        if self.busy:
            raise Busy("An experiment is running")
        self.task = asyncio.create_task(self.run_twin())

    async def reset_demo(self) -> str:
        """Undo applied fixes and clear runs, so the next judge sees the original app."""
        if self.busy and self._operation_task is not asyncio.current_task():
            raise Busy("An experiment is running")
        fixes.clear_overlay(self.target.overlay_file())
        self.refresh_graph()
        await self.runner.apply_overlay([n["id"] for n in self.graph["nodes"]], reset=True)
        self.results, self.analysis, self.fixes, self.applied_fixes = [], None, None, []
        self.score_history, self.current, self.phase, self.error = [], None, "idle", None
        self.campaign = {"status": "idle", "strategy": "ai", "budget": 0, "picks": [], "remaining": []}
        self.compare, self.twin, self.plan = None, None, None
        self.baseline, self._expected = None, None
        self.restored_from = None
        self.guard = Guard(self.guard.base)
        self.save()
        return "Fixes removed and runs cleared"

    # ---- kill switch ---------------------------------------------------------------------------------------
    async def killswitch(self) -> list[str]:
        """Stop whatever is running and put every service back the way it was before the fault."""
        fault = state.load_active_fault() if self.persist else None
        if fault:
            self._check_fault_scope(fault)
        want = fault["original"] if fault else \
            {s: {"state": "running", "connected": True, "cpus": None} for s in self.order}
        before = await self.runner.snapshot()
        if self.busy and self.task:
            self.task.cancel()  # the run restores in its finally block
            await asyncio.gather(self.task, return_exceptions=True)
        if self._operation_task and self._operation_task is not asyncio.current_task():
            operation = self._operation_task
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
        await self._restore(want)
        return [n for n in self.order if n in before and n in want and differs(before[n], want[n])]

    # ---- state ---------------------------------------------------------------------------------------------
    async def state_json(self) -> dict:
        st = await self._current_status()  # cached for Docker: /api/state answers in milliseconds
        ready, detail = st["ready"], st["detail"]
        if not self.busy:
            self._services = st["services"]
        cur = None
        if self.current:
            seconds_left = 0.0
            if self.phase == "observe" and self.guard.armed:
                seconds_left = self.current["fault_duration_s"] - self.current["elapsed_s"]
            cur = {**self.current, "guard": self.guard.view(seconds_left)}
        return {
            "target": self.target.name,
            "saved_at": time.time(),
            "restored_from": self.restored_from,
            "runner": {"kind": self.runner.kind, "ready": ready, "detail": detail},
            "services": self._services,
            "baseline": {k: self.baseline[k] for k in ("rps", "error_pct", "p95_ms", "healthy_services", "total_services")
                         if k in self.baseline} if self.baseline else None,
            "phase": self.phase,
            "busy": self.busy,
            "current": cur,
            "results": self.results,
            "campaign": {**self.campaign, "status": "stopped"} if self.campaign.get("status") == "running" and not self.busy
            else self.campaign,  # never report a campaign as running when nothing runs
            "twin": self.twin,
            "plan": self.plan,
            "analysis": self.analysis,
            "fixes": {**self.fixes, "fixes": [{k: v for k, v in x.items() if not k.startswith("_")}
                                              for x in self.fixes["fixes"]]} if self.fixes else None,
            "applied_fixes": self.applied_fixes,
            "score_history": self.score_history,
            "compare": self.compare,
            "cost": ledger.summary(),
            "error": self.error,
        }
