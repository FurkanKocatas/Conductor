"""Stripe webhook signature verification — guards the route that turns paid
workspaces on and off, so it is the highest-value thing to get right."""
import time

import pytest

from app.stripe_verify import StripeWebhookError, sign, verify

SECRET = "whsec_test_abc123"
BODY = b'{"id":"evt_1","type":"checkout.session.completed"}'


def _header(body=BODY, secret=SECRET, ts=None):
    return sign(body, int(time.time()) if ts is None else ts, secret)


def test_valid_signature_passes():
    verify(BODY, _header(), SECRET)


def test_tampered_body_rejected():
    h = _header()
    with pytest.raises(StripeWebhookError, match="no matching"):
        verify(BODY + b" ", h, SECRET)


def test_wrong_secret_rejected():
    with pytest.raises(StripeWebhookError, match="no matching"):
        verify(BODY, _header(secret="whsec_other"), SECRET)


def test_stale_timestamp_rejected():
    with pytest.raises(StripeWebhookError, match="tolerance"):
        verify(BODY, _header(ts=int(time.time()) - 3600), SECRET)


def test_future_timestamp_rejected():
    with pytest.raises(StripeWebhookError, match="tolerance"):
        verify(BODY, _header(ts=int(time.time()) + 3600), SECRET)


def test_missing_header_rejected():
    with pytest.raises(StripeWebhookError, match="missing"):
        verify(BODY, None, SECRET)


def test_header_without_timestamp_rejected():
    with pytest.raises(StripeWebhookError, match="no timestamp"):
        verify(BODY, "v1=deadbeef", SECRET)


def test_header_without_v1_rejected():
    with pytest.raises(StripeWebhookError, match="no v1"):
        verify(BODY, f"t={int(time.time())},v0=deadbeef", SECRET)


def test_timestamp_is_bound_to_signature():
    """Replaying a valid signature under a fresh timestamp must fail."""
    ts = int(time.time())
    good = sign(BODY, ts, SECRET).split("v1=")[1]
    with pytest.raises(StripeWebhookError, match="no matching"):
        verify(BODY, f"t={ts + 1},v1={good}", SECRET)


def test_multiple_v1_supports_rotation():
    ts = int(time.time())
    good = sign(BODY, ts, SECRET).split("v1=")[1]
    verify(BODY, f"t={ts},v1=bogus,v1={good}", SECRET)


def test_v0_ignored_but_v1_honored():
    ts = int(time.time())
    good = sign(BODY, ts, SECRET).split("v1=")[1]
    verify(BODY, f"t={ts},v0=whatever,v1={good}", SECRET)


def test_empty_secret_rejected():
    with pytest.raises(StripeWebhookError, match="not configured"):
        verify(BODY, _header(), "")


def test_uses_hex_not_base64():
    """Regression guard: Stripe's digest is hex. Svix's is base64 — mixing the
    two schemes would silently accept nothing (or, worse, the wrong thing)."""
    sig = sign(BODY, 1700000000, SECRET).split("v1=")[1]
    assert len(sig) == 64 and all(c in "0123456789abcdef" for c in sig)
