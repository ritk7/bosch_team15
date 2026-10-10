import pytest

from app import ledger, llm


@pytest.fixture(autouse=True)
def no_model_calls(monkeypatch, tmp_path):
    """Tests never call a real model (no cost, no network) and never touch the real ledger file."""
    async def unavailable(*args, **kwargs):
        raise llm.NoModelAvailable("disabled in tests")

    monkeypatch.setattr(llm, "complete", unavailable)
    monkeypatch.setattr(ledger, "LEDGER_FILE", tmp_path / "ledger.json")
