"""HTTP routes from docs/CONTRACT.md."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import config, ledger, llm, state, targets
from .campaign import Busy, Engine
from .runner import make_runner

TITLE = "Chaos Sentinel"
engine: Engine | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global engine
    target = targets.load()
    engine = Engine(target, make_runner(target))
    await engine.recover_on_startup()  # undo a fault left behind by a crash of this process
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
    return e


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
    return {"ok": True, "detail": f"Now testing the {target.title} ({engine.runner.kind})"}


@app.post("/api/target/start")
async def target_start() -> dict:
    return {"ok": True, "detail": await idle().runner.up()}


@app.post("/api/target/stop")
async def target_stop() -> dict:
    return {"ok": True, "detail": await idle().runner.down()}


@app.get("/api/catalog")
def catalog() -> dict:
    return {"experiments": eng().catalog}


@app.post("/api/baseline")
async def baseline(body: dict = Body(default={})) -> dict:
    duration = float(body.get("duration_s") or 5)
    if not 1 <= duration <= 60:
        raise HTTPException(422, "duration_s must be between 1 and 60")
    return await idle().measure_baseline(duration)


@app.post("/api/predict")
async def predict(body: dict = Body(...)) -> dict:
    e = eng()
    try:
        return await e.predict(str(body.get("experiment_id")))
    except KeyError:
        raise HTTPException(404, f"Unknown experiment '{body.get('experiment_id')}'") from None


@app.post("/api/run")
async def run(body: dict = Body(...)) -> dict:
    e = eng()
    fault_s = body.get("fault_duration_s")
    if fault_s is not None and not 1 <= float(fault_s) <= 30:
        raise HTTPException(422, "fault_duration_s must be between 1 and 30")
    try:
        return {"run_id": e.start_run(str(body.get("experiment_id")), str(body.get("label") or "before"),
                                      float(fault_s) if fault_s is not None else None)}
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
        idle().start_campaign(int(body.get("budget") or 5), strategy, int(body.get("seed") or 7))
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
    return await e.analyze()


@app.post("/api/fixes")
async def get_fixes() -> dict:
    e = idle()
    if not e.results:
        raise HTTPException(409, "Run at least one experiment first")
    fx = await e.recommend()
    return {**fx, "fixes": [{k: v for k, v in x.items() if not k.startswith("_")} for x in fx["fixes"]]}


@app.post("/api/apply-fixes")
async def apply_fixes(body: dict = Body(...)) -> dict:
    try:
        return await idle().apply_fixes([str(i) for i in body.get("fix_ids") or []])
    except ValueError as err:
        raise HTTPException(422, str(err)) from None


@app.post("/api/rerun")
async def rerun(body: dict = Body(default={})) -> dict:
    try:
        return {"ok": True, "experiments": idle().start_rerun(body.get("experiment_ids"))}
    except KeyError as err:
        raise HTTPException(404, f"Unknown experiment {err}") from None


@app.post("/api/reset")
async def reset_demo() -> dict:
    return {"ok": True, "detail": await idle().reset_demo()}


@app.get("/api/state")
async def get_state() -> dict:
    return await eng().state_json()


@app.get("/api/snapshot")
async def snapshot() -> dict:
    return state.load_snapshot() or await eng().state_json()


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
