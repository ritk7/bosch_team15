"""Cost accounting for every decision, including the ones made without a model.

Each entry carries two prices:
  cost_usd      what this decision actually cost
  baseline_usd  what the same decision would cost on config.BASELINE_TIER (the big model)
The gap between the two is the cost story for the pitch, so log rule and ML decisions too.
"""
from __future__ import annotations

import statistics
import json
import logging
import threading
import time
from dataclasses import asdict, dataclass

from . import config

MODEL_SOURCES = ("local", "small", "large")


@dataclass
class Entry:
    ts: float
    task: str
    source: str  # rules | ml | local | small | large | cache | retrieval | extractive
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    baseline_usd: float
    latency_ms: float
    cache: str
    run_id: str
    accepted: bool


LEDGER_FILE = config.CACHE_DIR / "cost_ledger.json"


def load_entries() -> list[Entry]:
    try:
        data = json.loads(LEDGER_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("Expected a list of ledger entries")
        return [Entry(**entry) for entry in data]
    except FileNotFoundError:
        return []
    except (OSError, ValueError, TypeError):
        logging.getLogger(__name__).warning("Could not restore cost ledger from %s", LEDGER_FILE)
        return []


_entries: list[Entry] = load_entries()
_lock = threading.Lock()
LEDGER_FILE = config.CACHE_DIR / "ledger.json"  # so the Cost view survives a restart of the tool


def _load() -> None:
    import json

    try:
        rows = json.loads(LEDGER_FILE.read_text(encoding="utf-8"))
        _entries.extend(Entry(**r) for r in rows[-5000:])
    except (OSError, ValueError, TypeError):
        pass


def _save() -> None:
    import json

    try:
        LEDGER_FILE.write_text(json.dumps([asdict(e) for e in _entries]), encoding="utf-8")
    except OSError:
        pass


_load()


def _save() -> None:
    tmp = LEDGER_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps([asdict(e) for e in _entries]), encoding="utf-8")
    tmp.replace(LEDGER_FILE)


def estimate_tokens(text: str) -> int:
    """Rough count (about 4 characters per token) for when a provider doesn't report usage."""
    return max(1, len(text) // 4)


def price(tier_name: str, tokens_in: int, tokens_out: int) -> float:
    t = config.tier(tier_name)
    return (tokens_in * t.usd_in_per_m + tokens_out * t.usd_out_per_m) / 1_000_000


def record(
    *,
    task: str,
    source: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
    latency_ms: float = 0.0,
    cache: str = "none",
    run_id: str = "",
    accepted: bool = True,
) -> Entry:
    entry = Entry(
        ts=time.time(),
        task=task,
        source=source,
        model=model,
        tokens_in=int(tokens_in),
        tokens_out=int(tokens_out),
        cost_usd=float(cost_usd),
        baseline_usd=price(config.BASELINE_TIER, tokens_in, tokens_out) if accepted else 0.0,
        latency_ms=float(latency_ms),
        cache=cache,
        run_id=run_id,
        accepted=accepted,
    )
    with _lock:
        _entries.append(entry)
        del _entries[:-5000]
        _save()
    return entry


def record_rule(
    *,
    task: str,
    tokens_in: int,
    tokens_out: int,
    run_id: str = "",
    source: str = "rules",
    model: str = "rules engine",
    latency_ms: float = 0.0,
) -> Entry:
    """A decision made without a model. It costs nothing; tokens describe the model call it replaced."""
    return record(
        task=task,
        source=source,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=0.0,
        latency_ms=latency_ms,
        run_id=run_id,
    )


def summary(run_id: str | None = None) -> dict:
    with _lock:
        es = [e for e in _entries if run_id is None or e.run_id == run_id]
    cost = sum(e.cost_usd for e in es)
    base = sum(e.baseline_usd for e in es)
    by_source: dict[str, dict] = {}
    for e in es:
        b = by_source.setdefault(e.source, {"calls": 0, "cost_usd": 0.0})
        b["calls"] += 1
        b["cost_usd"] = round(b["cost_usd"] + e.cost_usd, 6)
    latencies = [e.latency_ms for e in es if e.source in MODEL_SOURCES]
    return {
        "decisions": sum(1 for e in es if e.accepted),
        "model_calls": sum(1 for e in es if e.source in MODEL_SOURCES),
        "cost_usd": round(cost, 6),
        "baseline_usd": round(base, 6),
        "cost_inr": round(cost * config.USD_INR, 4),
        "baseline_inr": round(base * config.USD_INR, 4),
        "savings_pct": round(100 * (1 - cost / base), 1) if base > 0 else 0.0,
        "by_source": by_source,
        "cache_hits": sum(1 for e in es if e.source == "cache"),
        "p50_latency_ms": round(statistics.median(latencies), 1) if latencies else None,
        "baseline_model": config.tier(config.BASELINE_TIER).model,
        "usd_inr": config.USD_INR,
    }


def entries(limit: int = 300) -> list[dict]:
    with _lock:
        return [asdict(e) for e in reversed(_entries[-limit:])]


def reset() -> None:
    with _lock:
        _entries.clear()
        _save()
