from app import ledger


def test_model_cost_and_entries_survive_restart(monkeypatch):
    ledger.record(task="predict", source="small", model="test-model", tokens_in=10, tokens_out=20, cost_usd=.0123)
    before = ledger.summary()
    loaded = ledger.load_entries()
    assert len(loaded) == 1 and loaded[0].cost_usd == .0123
    monkeypatch.setattr(ledger, "_entries", loaded)
    assert ledger.summary() == before


def test_explicit_reset_is_persistent():
    ledger.record_rule(task="test", tokens_in=1, tokens_out=1)
    ledger.reset()
    assert ledger.load_entries() == []
    assert ledger.summary()["decisions"] == 0


def test_missing_ledger_starts_empty():
    assert ledger.load_entries() == []
