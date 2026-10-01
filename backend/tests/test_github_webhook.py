"""Tests for the GitHub App webhook receiver — signature verification,
installation events, and every marketplace_purchase action GitHub can
send (purchased, changed, cancelled, pending_change,
pending_change_cancelled). No network calls; nothing talks to the real
GitHub API.
"""
import hashlib
import hmac
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.pop("MONGO_URL", None)

from fastapi.testclient import TestClient  # noqa: E402

from server import app  # noqa: E402
from app.services import github_events_store as store  # noqa: E402

client = TestClient(app)


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _post_webhook(event: str, payload: dict, secret: str = "test_webhook_secret"):
    body = json.dumps(payload).encode()
    return client.post(
        "/api/github/webhook",
        content=body,
        headers={
            "content-type": "application/json",
            "x-github-event": event,
            "x-github-delivery": "test-delivery-id",
            "x-hub-signature-256": _sign(secret, body),
        },
    )


def test_webhook_without_secret_configured_returns_503(monkeypatch):
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    resp = client.post("/api/github/webhook", content=b"{}", headers={"x-github-event": "ping"})
    assert resp.status_code == 503
    assert resp.json()["error"] == "github_app_not_configured"


def test_webhook_rejects_bad_signature(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test_webhook_secret")
    resp = client.post(
        "/api/github/webhook",
        content=b'{"action": "purchased"}',
        headers={"x-github-event": "marketplace_purchase", "x-hub-signature-256": "sha256=deadbeef"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_signature"


def test_webhook_accepts_unknown_event_without_error(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test_webhook_secret")
    resp = _post_webhook("ping", {"zen": "hi"})
    assert resp.status_code == 200
    assert resp.json()["event"] == "ping"


def test_installation_created_is_recorded(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test_webhook_secret")
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "INSTALLATIONS_FILE", "installs.jsonl")

    resp = _post_webhook(
        "installation",
        {
            "action": "created",
            "installation": {
                "id": 12345,
                "account": {"login": "acme-corp", "type": "Organization"},
                "repository_selection": "selected",
            },
        },
    )
    assert resp.status_code == 200
    written = (tmp_path / "installs.jsonl").read_text()
    assert "acme-corp" in written
    assert '"action": "created"' in written
    assert "12345" in written


def test_marketplace_purchased_is_recorded_as_active(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test_webhook_secret")
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "MARKETPLACE_FILE", "mp.jsonl")

    resp = _post_webhook(
        "marketplace_purchase",
        {
            "action": "purchased",
            "effective_date": "2026-08-18T00:00:00+00:00",
            "marketplace_purchase": {
                "account": {"id": 777, "login": "first-customer", "type": "Organization"},
                "billing_cycle": "monthly",
                "unit_count": 1,
                "on_free_trial": False,
                "plan": {"id": 42, "name": "Pro"},
            },
        },
    )
    assert resp.status_code == 200
    written = json.loads((tmp_path / "mp.jsonl").read_text().strip().splitlines()[-1])
    assert written["action"] == "purchased"
    assert written["status"] == "active"
    assert written["account_login"] == "first-customer"
    assert written["plan_name"] == "Pro"
    assert written["unit_count"] == 1


def test_marketplace_cancelled_is_recorded_as_inactive(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test_webhook_secret")
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "MARKETPLACE_FILE", "mp.jsonl")

    resp = _post_webhook(
        "marketplace_purchase",
        {
            "action": "cancelled",
            "effective_date": "2026-09-18T00:00:00+00:00",
            "marketplace_purchase": {
                "account": {"id": 777, "login": "first-customer", "type": "Organization"},
                "plan": {"id": 1, "name": "Free"},
            },
            "previous_marketplace_purchase": {
                "plan": {"id": 42, "name": "Pro"},
            },
        },
    )
    assert resp.status_code == 200
    written = json.loads((tmp_path / "mp.jsonl").read_text().strip().splitlines()[-1])
    assert written["action"] == "cancelled"
    assert written["status"] == "cancelled"


import pytest  # noqa: E402


@pytest.mark.parametrize("action", ["changed", "pending_change", "pending_change_cancelled"])
def test_all_marketplace_actions_are_handled_without_error(monkeypatch, tmp_path, action):
    """Every action GitHub can send for marketplace_purchase must be
    accepted (200), never crash — GitHub disables/retries a webhook
    endpoint that errors on events it's subscribed to."""
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test_webhook_secret")
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "MARKETPLACE_FILE", "mp.jsonl")

    resp = _post_webhook(
        "marketplace_purchase",
        {
            "action": action,
            "effective_date": "2026-08-18T00:00:00+00:00",
            "marketplace_purchase": {
                "account": {"id": 1, "login": "x", "type": "User"},
                "plan": {"id": 1, "name": "Pro"},
            },
        },
    )
    assert resp.status_code == 200


def test_installation_repositories_event_is_recorded(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test_webhook_secret")
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "INSTALLATIONS_FILE", "installs.jsonl")

    resp = _post_webhook(
        "installation_repositories",
        {
            "action": "added",
            "installation": {
                "id": 999,
                "account": {"login": "solo-dev", "type": "User"},
                "repository_selection": "selected",
            },
        },
    )
    assert resp.status_code == 200
    written = (tmp_path / "installs.jsonl").read_text()
    assert "repositories_added" in written
