import pytest

from app import ledger, llm, mapper
from app.targets.hawkbit import HawkbitTarget
from app.targets.voting import VotingTarget


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """Tests never call a real model, never touch the real ledger, and never read the live demo's applied fixes or
    map corrections (they live next to the cloned apps)."""
    async def unavailable(*args, **kwargs):
        raise llm.NoModelAvailable("disabled in tests")

    monkeypatch.setattr(llm, "complete", unavailable)
    monkeypatch.setattr(ledger, "LEDGER_FILE", tmp_path / "ledger.json")
    for cls in (VotingTarget, HawkbitTarget):
        monkeypatch.setattr(cls, "overlay_file", lambda self: tmp_path / "no-overlay.yml")
    real = mapper.corrections_path
    monkeypatch.setattr(mapper, "corrections_path",
                        lambda d: real(d) if str(d).startswith(str(tmp_path)) else tmp_path / "no-map.json")
