"""Webhook signature verification — these guard a public, unauthenticated route."""
import base64
import time

import pytest

from app.svix_verify import WebhookError, sign, verify

SECRET = "whsec_" + base64.b64encode(b"super-secret-key-material").decode()
BODY = b'{"type":"organization.created","data":{"id":"org_123"}}'


def _headers(body=BODY, secret=SECRET, ts=None, svix_id="msg_1"):
    ts = int(time.time()) if ts is None else ts
    return {"svix-id": svix_id, "svix-timestamp": str(ts),
            "svix-signature": sign(body, svix_id, ts, secret)}


def test_valid_signature_passes():
    verify(BODY, _headers(), SECRET)


def test_tampered_body_rejected():
    h = _headers()
    with pytest.raises(WebhookError):
        verify(BODY + b"x", h, SECRET)


def test_wrong_secret_rejected():
    other = "whsec_" + base64.b64encode(b"different-key-material").decode()
    with pytest.raises(WebhookError):
        verify(BODY, _headers(secret=other), SECRET)


def test_stale_timestamp_rejected():
    old = int(time.time()) - 3600
    with pytest.raises(WebhookError, match="tolerance"):
        verify(BODY, _headers(ts=old), SECRET)


def test_future_timestamp_rejected():
    future = int(time.time()) + 3600
    with pytest.raises(WebhookError, match="tolerance"):
        verify(BODY, _headers(ts=future), SECRET)


def test_missing_headers_rejected():
    with pytest.raises(WebhookError, match="missing"):
        verify(BODY, {}, SECRET)


def test_signature_id_is_bound():
    """A signature made for one message id must not validate another."""
    h = _headers(svix_id="msg_1")
    h["svix-id"] = "msg_2"
    with pytest.raises(WebhookError):
        verify(BODY, h, SECRET)


def test_multiple_signatures_supports_rotation():
    ts = int(time.time())
    good = sign(BODY, "msg_1", ts, SECRET)
    h = {"svix-id": "msg_1", "svix-timestamp": str(ts),
         "svix-signature": f"v1,bogussignature {good}"}
    verify(BODY, h, SECRET)


def test_unknown_version_ignored():
    ts = int(time.time())
    h = {"svix-id": "msg_1", "svix-timestamp": str(ts),
         "svix-signature": "v2," + sign(BODY, "msg_1", ts, SECRET).split(",", 1)[1]}
    with pytest.raises(WebhookError):
        verify(BODY, h, SECRET)


def test_empty_secret_rejected():
    with pytest.raises(WebhookError):
        verify(BODY, _headers(), "")
