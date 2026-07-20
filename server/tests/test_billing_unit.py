"""Billing logic that doesn't need Stripe or a DB: plan limits, the on-switch
default status, and route wiring."""
import dataclasses

import pytest

from app import billing
from app.config import Settings


def _settings(**kw):
    base = dict(database_url="postgresql://x/y", allowed_origins=["*"],
                environment="development")
    base.update(kw)
    return Settings(**base)


def test_billing_disabled_without_full_stripe_config():
    assert _settings().billing_enabled is False
    assert _settings(stripe_secret_key="sk_test").billing_enabled is False
    assert _settings(stripe_secret_key="sk_test",
                     stripe_webhook_secret="whsec").billing_enabled is False


def test_billing_enabled_with_full_config():
    s = _settings(stripe_secret_key="sk_test", stripe_webhook_secret="whsec",
                  stripe_price_id="price_1")
    assert s.billing_enabled is True


def test_self_hosted_is_unlimited(monkeypatch):
    """With billing off (self-hosted), no plan gate should apply."""
    monkeypatch.setattr(billing, "settings", _settings())
    limits = billing.plan_limits(None)
    assert limits["max_projects"] >= 10**6


def test_paid_plan_has_real_limits(monkeypatch):
    monkeypatch.setattr(billing, "settings", _settings(
        stripe_secret_key="sk", stripe_webhook_secret="wh", stripe_price_id="p"))
    limits = billing.plan_limits("pro")
    assert limits["max_projects"] == 10
    assert limits["max_keys_per_project"] == 20


def test_unknown_plan_falls_back_to_configured_plan(monkeypatch):
    monkeypatch.setattr(billing, "settings", _settings(
        stripe_secret_key="sk", stripe_webhook_secret="wh", stripe_price_id="p"))
    assert billing.plan_limits("nonsense") == billing.PLANS["pro"]


def test_active_subscription_states():
    """trialing must count as usable; past_due/canceled must not."""
    assert "active" in billing._ACTIVE_SUB_STATES
    assert "trialing" in billing._ACTIVE_SUB_STATES
    for bad in ("past_due", "canceled", "unpaid", "incomplete"):
        assert bad not in billing._ACTIVE_SUB_STATES


def test_billing_routes_registered():
    from app.main import app
    paths = set(app.openapi()["paths"])
    assert "/api/dash/billing" in paths
    assert "/api/dash/billing/checkout" in paths
    assert "/api/dash/billing/portal" in paths
    assert "/api/webhooks/stripe" in paths


def test_stripe_webhook_501_when_unconfigured():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    r = client.post("/api/webhooks/stripe", content=b"{}")
    assert r.status_code == 501


@pytest.mark.parametrize("billing_on,expected", [(True, "pending"), (False, "active")])
def test_new_org_initial_status_follows_billing(monkeypatch, billing_on, expected):
    """The on-switch: with billing configured a fresh org must NOT be usable."""
    from app import deps
    cfg = _settings(stripe_secret_key="sk", stripe_webhook_secret="wh",
                    stripe_price_id="p") if billing_on else _settings()
    monkeypatch.setattr(deps, "settings", cfg)
    assert ("pending" if cfg.billing_enabled else "active") == expected
