"""Files that must survive a crash of the tool: the active fault (restored on startup) and the last full state (replay)."""
from __future__ import annotations

import json
from pathlib import Path

from . import config

ACTIVE_FAULT = config.CACHE_DIR / "active_fault.json"
LAST_STATE = config.CACHE_DIR / "last_state.json"


def _write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)  # atomic, so a crash never leaves half a file


def save_active_fault(data: dict) -> None:
    _write(ACTIVE_FAULT, data)


def load_active_fault() -> dict | None:
    try:
        return json.loads(ACTIVE_FAULT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def clear_active_fault() -> None:
    ACTIVE_FAULT.unlink(missing_ok=True)


def save_snapshot(data: dict) -> None:
    _write(LAST_STATE, data)


def load_snapshot() -> dict | None:
    try:
        return json.loads(LAST_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
