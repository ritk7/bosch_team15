"""HTTP routes. Stub: the skeleton starts, the real routes are listed in docs/CONTRACT.md and still to be written."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import config, ledger, llm

TITLE = "Chaos Sentinel"

app = FastAPI(title=TITLE)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "title": TITLE, "tiers": await llm.status(),
            "runner": {"kind": "sim", "ready": False, "detail": "Runner not built yet"}}


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
