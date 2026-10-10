from fastapi.testclient import TestClient

from app.main import app


def test_health_and_ledger_start():
    client = TestClient(app)
    assert client.get("/api/health").json()["ok"] is True
    assert "summary" in client.get("/api/ledger").json()
