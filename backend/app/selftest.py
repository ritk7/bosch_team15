"""`python dev.py selftest`: one real crash on the real target, with PASS/FAIL per step. Needs Docker and targets/voting."""
from __future__ import annotations

import asyncio
import sys

from . import config, state, targets
from .campaign import Engine
from .docker_runner import DockerRunner, docker_bin


def line(ok: bool, step: str, detail: str) -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {step}: {detail}", flush=True)
    return ok


async def main() -> int:
    print("Chaos Sentinel selftest (real Docker)\n")
    target = targets.load()
    if not docker_bin():
        line(False, "docker", "docker CLI not found; install and start Docker Desktop")
        return 2
    if not (config.TARGET_DIR / "docker-compose.yml").exists():
        line(False, "target", f"{config.TARGET_DIR} not cloned; see README")
        return 2
    runner = DockerRunner(target)
    engine = Engine(target, runner)
    await engine.recover_on_startup()

    print("  ...   starting the app (first build takes a few minutes)", flush=True)
    try:
        await runner.up()
        for svc in engine.order:
            await runner._wait_up(svc, 90)
    except RuntimeError as err:
        line(False, "app up", str(err))
        return 1
    services = await runner.services()
    up = [s["id"] for s in services if s["state"] == "running"]
    ok = line(len(up) == len(services), "app up", f"{len(up)}/{len(services)} services running ({', '.join(up)})")

    g = engine.graph
    ok &= line(len(g["nodes"]) == 5 and any(e["kind"] == "dataflow" for e in g["edges"]), "map built",
               f"{len(g['nodes'])} services, {len(g['edges'])} edges")

    b = await engine.measure_baseline(5)
    ok &= line(b["error_pct"] == 0 and b["votes_stored"] >= 0.9 * b["votes_sent"], "baseline",
               f"{b['rps']} votes/s, {b['error_pct']}% errors, p95 {b['p95_ms']} ms, {b['votes_stored']}/{b['votes_sent']} stored")

    exp = engine.experiment("crash-redis")
    task = asyncio.create_task(engine.run("selftest", exp, "selftest", 10.0))
    saw_down = False
    while not task.done():
        if engine.phase in ("observe", "restore") and engine.guard.armed:
            snap = await runner.snapshot()
            saw_down = saw_down or snap["redis"]["state"] != "running"
        await asyncio.sleep(0.5)
    result = task.result()
    if result is None:
        line(False, "experiment", engine.error or "failed")
        return 1
    o = result["outcome"]
    ok &= line(saw_down and o["errors"] > 0, "fault injected", f"redis killed, {o['errors']} errors ({o['error_pct']}%)")
    after = await runner.snapshot()
    all_back = all(v["state"] == "running" for v in after.values())
    ok &= line(all_back and state.load_active_fault() is None, "guard restored",
               f"guard {'tripped: ' + (o['guard_reason'] or '') if o['guard_tripped'] else 'not tripped'}; "
               f"all services running: {all_back}")
    ok &= line(o["recovery_s"] is not None, "recovery measured",
               f"recovery {o['recovery_s']} s, {o['lost']} votes lost silently, {o['stored']}/{o['acked']} acknowledged votes stored")
    print(f"\n{'PASS' if ok else 'FAIL'}: selftest {'passed' if ok else 'failed'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
