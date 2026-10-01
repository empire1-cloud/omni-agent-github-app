"""Event storage location and durability reporting (serverless hosts)."""
from pathlib import Path

from fastapi.testclient import TestClient

from app.core import storage


def test_local_default_is_durable(monkeypatch):
    for var in ("VERCEL", "MONGO_URL", "OMNI_AGENT_DATA_DIR"):
        monkeypatch.delenv(var, raising=False)
    assert storage.data_dir() == storage.DEFAULT_DATA_DIR
    assert storage.event_storage_durable() is True


def test_vercel_without_mongo_uses_tmp_and_reports_not_durable(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("OMNI_AGENT_DATA_DIR", raising=False)
    assert storage.data_dir() == Path("/tmp/omni-agent-data")
    assert storage.event_storage_durable() is False
    monkeypatch.setenv("MONGO_URL", "mongodb://example")
    assert storage.event_storage_durable() is True


def test_append_failure_does_not_raise(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert storage.append_jsonl(blocker / "sub" / "a.jsonl", {"event": "x"}) is False


def test_health_reports_storage_durability(monkeypatch):
    from server import app
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("OMNI_AGENT_DATA_DIR", raising=False)
    body = TestClient(app).get("/api/health").json()
    assert body["event_storage_durable"] is False
