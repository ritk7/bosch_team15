"""`python dev.py selftest`: one real crash on the real target, with PASS/FAIL per step. Needs Docker and targets/voting."""
from __future__ import annotations

import asyncio
import secrets
import sys

from . import config, state, targets
from .campaign import Engine
from .docker_runner import DockerRunner, docker_bin


def line(ok: bool, step: str, detail: str) -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {step}: {detail}", flush=True)
    return ok


async def main() -> int:
    if "--target" in sys.argv:
        config.TARGET = sys.argv[sys.argv.index("--target") + 1]
        config.TARGET_DIR = (config.REPO / "targets" / config.TARGET).resolve()
    target = targets.load()
    print(f"Chaos Sentinel selftest on the {target.title} (real Docker)\n")
    if not docker_bin():
        line(False, "docker", "docker CLI not found; install and start Docker Desktop")
        return 2
    if not (config.TARGET_DIR / "docker-compose.yml").exists():
        line(False, "target", f"{config.TARGET_DIR} not cloned; see README")
        return 2
    runner = DockerRunner(target)
    engine = Engine(target, runner)
    await engine.recover_on_startup()
    engine.load_snapshot(state.load_snapshot(target.name))  # self-tests append evidence, never replace demo history

    print("  ...   starting the app (first build takes a few minutes)", flush=True)
    try:
        await runner.up()
        for svc in engine.order:
            await runner._wait_up(svc, 180)  # hawkBit's JVM under emulation needs a while
    except RuntimeError as err:
        line(False, "app up", str(err))
        return 1
    services = await runner.services()
    up = [s["id"] for s in services if s["state"] == "running"]
    ok = line(len(up) == len(services), "app up", f"{len(up)}/{len(services)} services running ({', '.join(up)})")

    g = engine.graph
    ok &= line(len(g["nodes"]) >= 3 and any(e["kind"] == "dataflow" for e in g["edges"]), "map built",
               f"{len(g['nodes'])} services, {len(g['edges'])} edges")

    u = target.unit["plural"]
    try:
        b = await engine.measure_baseline(5)
    except Exception as err:  # e.g. the target's API refused the setup calls
        line(False, "baseline", f"{type(err).__name__}: {err}")
        return 1
    ok &= line(b["error_pct"] == 0 and b["votes_stored"] >= 0.9 * b["votes_sent"], "baseline",
               f"{b['rps']} {u}/s, {b['error_pct']}% errors, p95 {b['p95_ms']} ms, {b['votes_stored']}/{b['votes_sent']} {target.unit['stored']}")

    exp = engine.experiment(target.selftest_experiment)
    victim = exp["target"]
    # hawkBit persists controller IDs. A fixed "selftest" prefix caused HTTP 409s on subsequent runs, falsely
    # attributing duplicate-device errors (even before injection) to the fault being tested.
    run_id = f"selftest-{secrets.token_hex(8)}"
    task = asyncio.create_task(engine.run(run_id, exp, "selftest", 10.0))
    saw_down = False
    while not task.done():
        if engine.phase in ("observe", "restore") and engine.guard.armed:
            snap = await runner.snapshot()
            saw_down = saw_down or snap[victim]["state"] != "running"
        await asyncio.sleep(0.5)
    result = task.result()
    if result is None:
        line(False, "experiment", engine.error or "failed")
        return 1
    o = result["outcome"]
    ok &= line(saw_down or o["errors"] > 0 or o["guard_tripped"], "fault injected",
               f"{victim} killed, {o['errors']} errors ({o['error_pct']}%), peak p95 {o['peak_p95_ms']} ms")
    after = await runner.snapshot()
    all_back = all(v["state"] == "running" for v in after.values())
    ok &= line(all_back and state.load_active_fault() is None, "guard restored",
               f"guard {'tripped: ' + (o['guard_reason'] or '') if o['guard_tripped'] else 'not tripped'}; "
               f"all services running: {all_back}")
    ok &= line(o["recovery_s"] is not None, "recovery measured",
               f"recovery {o['recovery_s']} s, {o['lost']} {u} {target.unit['lost']}, {o['stored']}/{o['acked']} acknowledged {u} {target.unit['stored']}")
    print(f"\n{'PASS' if ok else 'FAIL'}: selftest {'passed' if ok else 'failed'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
