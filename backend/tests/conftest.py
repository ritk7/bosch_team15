import pytest

from app import llm


@pytest.fixture(autouse=True)
def no_model_calls(monkeypatch):
    """Tests never call a real model (no cost, no network): agents take their rules fallback."""
    async def unavailable(*args, **kwargs):
        raise llm.NoModelAvailable("disabled in tests")

    monkeypatch.setattr(llm, "complete", unavailable)
