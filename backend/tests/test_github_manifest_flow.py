"""Tests for the GitHub App manifest registration flow (GET /app/new,
GET /manifest-callback) and the manifest content itself.
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from server import app  # noqa: E402
from app.core import github_app as gh_config  # noqa: E402
from app.services import github_manifest_service as manifest_svc  # noqa: E402
from app.routers import github_app as github_app_router  # noqa: E402

client = TestClient(app)


def test_manifest_has_required_fields_and_v2_permissions(monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "https://api.example.com")
    monkeypatch.setenv("FRONTEND_BASE_URL", "https://omni-agent.example.com")
    m = gh_config.build_manifest()
    assert m["hook_attributes"]["url"] == "https://api.example.com/api/github/webhook"
    assert m["redirect_url"] == "https://api.example.com/api/github/manifest-callback"
    assert m["public"] is True
    assert set(m["default_events"]) == {"installation", "installation_repositories", "marketplace_purchase"}
    # V2 remains least-privilege for the declared PR/check capabilities.
    assert m["default_permissions"] == {
        "metadata": "read",
        "contents": "read",
        "pull_requests": "write",
        "checks": "write",
        "statuses": "write",
    }


def test_registration_url_uses_org_when_configured(monkeypatch):
    monkeypatch.delenv("GITHUB_APP_OWNER_ORG", raising=False)
    assert gh_config.manifest_registration_url() == "https://github.com/settings/apps/new"
    monkeypatch.setenv("GITHUB_APP_OWNER_ORG", "empire1-cloud")
    assert (
        gh_config.manifest_registration_url()
        == "https://github.com/organizations/empire1-cloud/settings/apps/new"
    )


def test_new_app_form_embeds_manifest_and_state():
    resp = client.get("/api/github/app/new")
    assert resp.status_code == 200
    assert "Continue to GitHub" in resp.text
    assert "hook_attributes" in resp.text


def test_manifest_callback_rejects_missing_state():
    resp = client.get("/api/github/manifest-callback?code=abc123")
    assert resp.status_code == 400


def test_manifest_callback_rejects_replayed_state():
    state = manifest_svc.issue_state()
    assert manifest_svc.consume_state(state) is True
    # second use of the same state must fail (single-use / anti-replay)
    assert manifest_svc.consume_state(state) is False


def test_manifest_callback_happy_path(monkeypatch):
    state = manifest_svc.issue_state()

    async def fake_exchange(code):
        assert code == "the-temp-code"
        return {
            "id": 123456,
            "slug": "omni-agent",
            "pem": "-----BEGIN RSA PRIVATE KEY-----\nFAKE\n-----END RSA PRIVATE KEY-----",
            "webhook_secret": "fake_webhook_secret",
            "client_id": "Iv1.fakeclientid",
            "client_secret": "fake_client_secret",
        }

    monkeypatch.setattr(github_app_router, "exchange_manifest_code", fake_exchange)

    resp = client.get(f"/api/github/manifest-callback?code=the-temp-code&state={state}")
    assert resp.status_code == 200
    assert "GITHUB_APP_ID" in resp.text
    assert "123456" in resp.text
    assert "fake_webhook_secret" in resp.text


def test_state_verifies_on_another_instance(monkeypatch):
    # serverless: the callback can land on a fresh process with no memory of the form
    monkeypatch.setenv("MANIFEST_STATE_SECRET", "shared-secret")
    state = manifest_svc.issue_state()
    monkeypatch.setattr(manifest_svc, "_used_states", set())
    monkeypatch.setattr(manifest_svc, "_PROCESS_KEY", b"a different process key")
    assert manifest_svc.consume_state(state) is True


def test_state_rejects_tampering_and_expiry(monkeypatch):
    monkeypatch.setenv("MANIFEST_STATE_SECRET", "shared-secret")
    state = manifest_svc.issue_state()
    expires, nonce, sig = state.split(".")
    assert manifest_svc.consume_state(f"{int(expires) + 999}.{nonce}.{sig}") is False
    assert manifest_svc.consume_state("garbage") is False
    monkeypatch.setattr(manifest_svc.time, "time", lambda: int(expires) + 1)
    assert manifest_svc.consume_state(state) is False


def test_state_signed_with_another_secret_is_rejected(monkeypatch):
    monkeypatch.setenv("MANIFEST_STATE_SECRET", "one")
    state = manifest_svc.issue_state()
    monkeypatch.setenv("MANIFEST_STATE_SECRET", "two")
    assert manifest_svc.consume_state(state) is False
