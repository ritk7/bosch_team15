import pytest

from app import ledger, llm, mapper, state
from app.cache import ResponseCache
from app.targets.hawkbit import HawkbitTarget
from app.targets.voting import VotingTarget


@pytest.fixture(autouse=True)
def isolated_runtime_files(tmp_path, monkeypatch):
    """Tests must never alter the live demo's recovery record, snapshots, cache or cost ledger."""
    monkeypatch.setattr(state, "ACTIVE_FAULT", tmp_path / "active.json")
    monkeypatch.setattr(state, "LAST_STATE", tmp_path / "snapshot.json")
    monkeypatch.setattr(ledger, "LEDGER_FILE", tmp_path / "ledger.json")
    monkeypatch.setattr(ledger, "_entries", [])
    monkeypatch.setattr(llm, "cache", ResponseCache(tmp_path / "cache.json"))


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """Tests never call a real model, never touch the real ledger, and never read the live demo's applied fixes or
    map corrections (they live next to the cloned apps)."""
    async def unavailable(*args, **kwargs):
        raise llm.NoModelAvailable("disabled in tests")

    monkeypatch.setattr(llm, "complete", unavailable)
    for cls in (VotingTarget, HawkbitTarget):
        monkeypatch.setattr(cls, "overlay_file", lambda self: tmp_path / "no-overlay.yml")
    real = mapper.corrections_path
    monkeypatch.setattr(mapper, "corrections_path",
                        lambda d: real(d) if str(d).startswith(str(tmp_path)) else tmp_path / "no-map.json")
