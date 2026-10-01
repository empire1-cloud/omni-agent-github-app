"""Tests for the self-serve billing routes.

No network calls to Stripe are made — `stripe.checkout.Session.create` is
monkeypatched so these run offline and never touch a real Stripe account.
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.pop("MONGO_URL", None)

from fastapi.testclient import TestClient  # noqa: E402

from server import app  # noqa: E402
from app.services import stripe_service  # noqa: E402

client = TestClient(app)


def test_plans_lists_pro_and_team_not_purchasable_by_default(monkeypatch):
    monkeypatch.delenv("STRIPE_PRICE_PRO", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_TEAM", raising=False)
    resp = client.get("/api/billing/plans")
    assert resp.status_code == 200
    plans = resp.json()["plans"]
    assert plans["pro"]["monthly_usd"] == 49
    assert plans["team"]["monthly_usd"] == 299
    assert plans["pro"]["purchasable"] is False


def test_checkout_without_stripe_configured_returns_503(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    resp = client.post("/api/billing/checkout", json={"plan": "pro"})
    assert resp.status_code == 503
    assert resp.json()["error"] == "billing_not_configured"


def test_checkout_rejects_unknown_plan():
    resp = client.post("/api/billing/checkout", json={"plan": "not-a-real-plan"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_plan"


def test_checkout_happy_path_returns_stripe_url(monkeypatch, tmp_path):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("STRIPE_PRICE_PRO", "price_fake_pro")
    monkeypatch.setenv("APP_BASE_URL", "https://omni-agent.example.com")

    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="cs_test_123", url="https://checkout.stripe.com/pay/cs_test_123")

    monkeypatch.setattr(stripe_service.stripe.checkout.Session, "create", fake_create)

    # Redirect the JSONL audit write into a temp dir so the test doesn't
    # touch the real backend/data directory.
    import app.services.customer_store as customer_store

    monkeypatch.setattr(customer_store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(customer_store, "CUSTOMERS_FILE", "customers.jsonl")

    resp = client.post(
        "/api/billing/checkout",
        json={"plan": "pro", "customer_email": "first@customer.com"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["checkout_url"] == "https://checkout.stripe.com/pay/cs_test_123"
    assert body["session_id"] == "cs_test_123"

    assert captured["mode"] == "subscription"
    assert captured["line_items"] == [{"price": "price_fake_pro", "quantity": 1}]
    assert captured["customer_email"] == "first@customer.com"
    assert captured["success_url"].startswith("https://omni-agent.example.com/success")

    written = (tmp_path / "customers.jsonl").read_text()
    assert "checkout_started" in written
    assert "first@customer.com" in written


def test_webhook_without_secret_returns_503():
    resp = client.post("/api/billing/webhook", data=b"{}", headers={"stripe-signature": "x"})
    assert resp.status_code == 503


@pytest.mark.parametrize("bad_plan", ["", "enterprise", "free"])
def test_only_pro_and_team_are_self_serve(bad_plan):
    """Enterprise/Free must never be purchasable through this endpoint —
    Enterprise is sales-assisted, Free needs no checkout."""
    resp = client.post("/api/billing/checkout", json={"plan": bad_plan})
    assert resp.status_code == 400
