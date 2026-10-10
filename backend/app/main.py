"""HTTP routes from docs/CONTRACT.md."""
from __future__ import annotations

from contextlib import asynccontextmanager
import math

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import config, ledger, llm, report, state, targets
from .campaign import Busy, Engine
from .runner import make_runner

TITLE = "Chaos Sentinel"
engine: Engine | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global engine
    fault = state.load_active_fault()
    target = targets.load(fault["target"] if fault and fault.get("target") else None)
    engine = Engine(target, make_runner(target))
    await engine.recover_on_startup()  # undo a fault left behind by a crash of this process
    engine.load_snapshot(state.load_snapshot(target.name))  # keep the demo's runs and findings across a restart
    yield
    if engine.busy:
        await engine.killswitch()


app = FastAPI(title=TITLE, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def eng() -> Engine:
    if engine is None:
        raise HTTPException(503, "The engine is starting")
    return engine


def idle() -> Engine:
    e = eng()
    if e.busy:
        raise HTTPException(409, "An experiment is running. Wait for it or press the kill switch.")
    if e.persist and state.load_active_fault():
        raise HTTPException(409, "A previous fault still needs restoration. Press the kill switch before another operation.")
    return e


def number(body: dict, key: str, default: float, low: float, high: float, *, integer: bool = False):
    value = body.get(key, default)
    try:
        if isinstance(value, bool):
            raise ValueError
        value = float(value)
        if not math.isfinite(value) or not low <= value <= high or (integer and not value.is_integer()):
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        raise HTTPException(422, f"{key} must be {'an integer' if integer else 'a number'} between {low:g} and {high:g}") from None
    return int(value) if integer else value


async def exclusive(e: Engine, action):
    try:
        async with e.operation():
            return await action()
    except Busy as err:
        raise HTTPException(409, str(err)) from None


@app.get("/api/health")
async def health() -> dict:
    ready, detail = (await eng().runner.ready()) if engine else (False, "Starting")
    return {"ok": True, "title": TITLE, "tiers": await llm.status(),
            "runner": {"kind": engine.runner.kind if engine else "sim", "ready": ready, "detail": detail}}


@app.get("/api/target")
async def target() -> dict:
    e = eng()
    compose = e.target.compose_file()
    try:
        compose_name = str(compose.relative_to(config.REPO))
    except ValueError:
        compose_name = str(compose)
    return {"name": e.target.name, "compose_file": compose_name, "graph": e.graph,
            "assumptions": e.target.assumptions(e.rate), "services": await e.runner.services(), "unit": e.target.unit}


@app.get("/api/targets")
def list_targets() -> dict:
    return {"targets": [{"name": n, "title": cls.title} for n, cls in targets.TARGETS.items()],
            "current": eng().target.name}


@app.post("/api/target/select")
async def select_target(body: dict = Body(...)) -> dict:
    """Switch the app under test. In Docker mode the other target's containers must be started separately."""
    global engine
    idle()
    name = str(body.get("name"))
    if name not in targets.TARGETS:
        raise HTTPException(404, f"Unknown target '{name}'")
    target = targets.load(name)
    engine = Engine(target, make_runner(target))
    engine.load_snapshot(state.load_snapshot(target.name))
    return {"ok": True, "detail": f"Now testing the {target.title} ({engine.runner.kind})"}


@app.post("/api/target/start")
async def target_start() -> dict:
    e = idle()
    return {"ok": True, "detail": await exclusive(e, e.runner.up)}


@app.post("/api/target/stop")
async def target_stop() -> dict:
    e = idle()
    e.baseline = None
    return {"ok": True, "detail": await exclusive(e, e.runner.down)}


@app.get("/api/catalog")
def catalog() -> dict:
    return {"experiments": eng().catalog}


@app.post("/api/baseline")
async def baseline(body: dict = Body(default={})) -> dict:
    duration = number(body, "duration_s", 5, 1, 60)
    e = idle()
    return await exclusive(e, lambda: e.measure_baseline(duration))


@app.post("/api/predict")
async def predict(body: dict = Body(...)) -> dict:
    e = idle()
    try:
        return await exclusive(e, lambda: e.predict(str(body.get("experiment_id"))))
    except KeyError:
        raise HTTPException(404, f"Unknown experiment '{body.get('experiment_id')}'") from None


@app.post("/api/run")
async def run(body: dict = Body(...)) -> dict:
    e = eng()
    fault_s = body.get("fault_duration_s")
    if fault_s is not None:
        fault_s = number(body, "fault_duration_s", 10, 1, 30)
    try:
        return {"run_id": e.start_run(str(body.get("experiment_id")), str(body.get("label") or "before"),
                                      fault_s)}
    except KeyError:
        raise HTTPException(404, f"Unknown experiment '{body.get('experiment_id')}'") from None
    except Busy as err:
        raise HTTPException(409, str(err)) from None


@app.post("/api/killswitch")
async def killswitch() -> dict:
    return {"ok": True, "restored": await eng().killswitch()}


@app.post("/api/campaign/start")
async def campaign_start(body: dict = Body(...)) -> dict:
    strategy = str(body.get("strategy") or "ai")
    if strategy not in ("ai", "random"):
        raise HTTPException(422, "strategy must be ai or random")
    try:
        e = idle()
        e.start_campaign(number(body, "budget", 5, 1, max(5, len(e.catalog)), integer=True), strategy,
                         number(body, "seed", 7, 0, 2**32 - 1, integer=True))
    except Busy as err:
        raise HTTPException(409, str(err)) from None
    return {"ok": True}


@app.post("/api/campaign/stop")
async def campaign_stop() -> dict:
    return {"ok": True, "restored": await eng().stop_campaign()}


@app.post("/api/twin")
async def twin() -> dict:
    """Rehearse every experiment on the digital twin in seconds; results land in /state.twin and /state.compare."""
    try:
        idle().start_twin()
    except Busy as err:
        raise HTTPException(409, str(err)) from None
    return {"ok": True}


@app.post("/api/analyze")
async def analyze() -> dict:
    e = idle()
    if not e.results:
        raise HTTPException(409, "Run at least one experiment first")
    return await exclusive(e, e.analyze)


@app.post("/api/fixes")
async def get_fixes() -> dict:
    e = idle()
    if not e.results:
        raise HTTPException(409, "Run at least one experiment first")
    fx = await exclusive(e, e.recommend)
    return {**fx, "fixes": [{k: v for k, v in x.items() if not k.startswith("_")} for x in fx["fixes"]]}


@app.post("/api/apply-fixes")
async def apply_fixes(body: dict = Body(...)) -> dict:
    try:
        ids = body.get("fix_ids", [])
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise ValueError("fix_ids must be a list of strings")
        e = idle()
        return await exclusive(e, lambda: e.apply_fixes(ids))
    except ValueError as err:
        raise HTTPException(422, str(err)) from None


@app.post("/api/rerun")
async def rerun(body: dict = Body(default={})) -> dict:
    ids = body.get("experiment_ids")
    if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)):
        raise HTTPException(422, "experiment_ids must be a list of strings")
    try:
        return {"ok": True, "experiments": idle().start_rerun(ids)}
    except KeyError as err:
        raise HTTPException(404, f"Unknown experiment {err}") from None


@app.post("/api/reset")
async def reset_demo() -> dict:
    e = idle()
    return {"ok": True, "detail": await exclusive(e, e.reset_demo)}


@app.get("/api/state")
async def get_state() -> dict:
    return await eng().state_json()


@app.get("/api/report")
def get_report() -> dict:
    e = eng()
    return {"markdown": report.findings_report(e), "score": e.analysis["score"] if e.analysis else None,
            "score_history": e.score_history}


@app.get("/api/report.md")
def report_md() -> PlainTextResponse:
    e = eng()
    return PlainTextResponse(report.findings_report(e), media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="{e.target.name}-findings-report.md"'})


@app.get("/api/architecture.md")
def architecture_md() -> PlainTextResponse:
    return PlainTextResponse(report.architecture(eng()), media_type="text/markdown",
                             headers={"Content-Disposition": 'attachment; filename="architecture.md"'})


@app.get("/metrics")
async def metrics() -> PlainTextResponse:
    """Prometheus text format, so Prometheus/Grafana (docker-compose.observability.yml) can chart our measurements."""
    e = eng()
    st = await e.state_json()
    t = e.target.name
    out = ["# TYPE chaos_service_up gauge"]
    for s in st["services"]:
        out.append(f'chaos_service_up{{target="{t}",service="{s["id"]}"}} {1 if s["state"] == "running" else 0}')
        if s["cpu_pct"] is not None:
            out.append(f'chaos_container_cpu_percent{{target="{t}",service="{s["id"]}"}} {s["cpu_pct"]}')
            out.append(f'chaos_container_memory_mb{{target="{t}",service="{s["id"]}"}} {s["mem_mb"]}')
    cur = st["current"]
    if cur:
        for k, v in cur["counters"].items():
            out.append(f'chaos_units{{target="{t}",kind="{k}"}} {v}')
        if cur["timeline"]:
            p = cur["timeline"][-1]
            out += [f'chaos_error_rate{{target="{t}"}} {p["error_rate"]}', f'chaos_p95_ms{{target="{t}"}} {p["p95_ms"]}']
        out.append(f'chaos_guard_tripped{{target="{t}"}} {1 if cur["guard"]["tripped"] else 0}')
        out.append(f'chaos_fault_active{{target="{t}"}} {1 if st["phase"] in ("inject", "observe") else 0}')
    if st["analysis"]:
        out.append(f'chaos_resilience_score{{target="{t}"}} {st["analysis"]["score"]["value"]}')
    out.append(f'chaos_model_cost_inr_total {st["cost"]["cost_inr"]}')
    return PlainTextResponse("\n".join(out) + "\n", media_type="text/plain; version=0.0.4")


@app.get("/api/snapshot")
async def snapshot() -> dict:
    return state.load_snapshot(eng().target.name) or await eng().state_json()


@app.get("/api/ledger")
def get_ledger() -> dict:
    return {"summary": ledger.summary(), "entries": ledger.entries()}


@app.post("/api/ledger/reset")
def reset_ledger() -> dict:
    ledger.reset()
    return {"ok": True}


DIST = config.ROOT.parent / "frontend" / "dist"
if DIST.exists():  # `python dev.py serve` builds the UI so everything runs on one port
    app.mount("/", StaticFiles(directory=DIST, html=True), name="ui")
