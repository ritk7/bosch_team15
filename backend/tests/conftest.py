import pytest

from app import ledger, llm, state
from app.cache import ResponseCache


@pytest.fixture(autouse=True)
def isolated_runtime_files(tmp_path, monkeypatch):
    """Tests must never alter the live demo's recovery record, snapshots, cache or cost ledger."""
    monkeypatch.setattr(state, "ACTIVE_FAULT", tmp_path / "active.json")
    monkeypatch.setattr(state, "LAST_STATE", tmp_path / "snapshot.json")
    monkeypatch.setattr(ledger, "LEDGER_FILE", tmp_path / "ledger.json")
    monkeypatch.setattr(ledger, "_entries", [])
    monkeypatch.setattr(llm, "cache", ResponseCache(tmp_path / "cache.json"))


@pytest.fixture(autouse=True)
def no_model_calls(monkeypatch):
    """Tests never call a real model (no cost, no network): agents take their rules fallback."""
    async def unavailable(*args, **kwargs):
        raise llm.NoModelAvailable("disabled in tests")

    monkeypatch.setattr(llm, "complete", unavailable)
