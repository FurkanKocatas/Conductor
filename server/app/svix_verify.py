"""Svix webhook signature verification (used by Clerk, and later Stripe-via-Svix).

Implemented directly rather than pulling the SDK: the scheme is small, and an
in-repo version is unit-testable without network or vendor mocks.

Scheme:
  signed_payload = f"{svix-id}.{svix-timestamp}.{raw_body}"
  expected       = base64(HMAC_SHA256(secret_bytes, signed_payload))
  the `svix-signature` header is a space-separated list of "v1,<sig>" entries;
  the request is valid if ANY v1 entry matches (supports secret rotation).

The secret arrives as "whsec_<base64>"; the part after the prefix is the raw key.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time


class WebhookError(Exception):
    """Signature/timestamp verification failed."""


DEFAULT_TOLERANCE_S = 300      # 5 minutes, matching Svix's own default


def _secret_bytes(secret: str) -> bytes:
    if not secret:
        raise WebhookError("webhook secret is not configured")
    raw = secret.split("_", 1)[1] if secret.startswith("whsec_") else secret
    try:
        return base64.b64decode(raw)
    except Exception as e:  # noqa: BLE001
        raise WebhookError("malformed webhook secret") from e


def verify(body: bytes, headers: dict, secret: str,
           tolerance_s: int = DEFAULT_TOLERANCE_S, now: float | None = None) -> None:
    """Raise WebhookError unless `body` carries a valid, fresh Svix signature.

    `headers` should be a case-insensitive mapping (Starlette's request.headers
    already is). Returns None on success.
    """
    svix_id = headers.get("svix-id")
    svix_ts = headers.get("svix-timestamp")
    svix_sig = headers.get("svix-signature")
    if not (svix_id and svix_ts and svix_sig):
        raise WebhookError("missing svix headers")

    # Replay window — reject stale or far-future timestamps.
    try:
        ts = int(svix_ts)
    except ValueError as e:
        raise WebhookError("invalid svix timestamp") from e
    current = time.time() if now is None else now
    if abs(current - ts) > tolerance_s:
        raise WebhookError("webhook timestamp outside tolerance")

    signed = b"%s.%s." % (svix_id.encode(), svix_ts.encode()) + body
    expected = base64.b64encode(
        hmac.new(_secret_bytes(secret), signed, hashlib.sha256).digest()).decode()

    # Header may list several signatures; any valid v1 entry accepts the request.
    for part in svix_sig.split(" "):
        version, _, candidate = part.partition(",")
        if version != "v1" or not candidate:
            continue
        if hmac.compare_digest(candidate, expected):   # constant-time
            return
    raise WebhookError("no matching signature")


def sign(body: bytes, svix_id: str, timestamp: int, secret: str) -> str:
    """Produce a `svix-signature` header value. Test helper / fixture use."""
    signed = b"%s.%s." % (svix_id.encode(), str(timestamp).encode()) + body
    digest = hmac.new(_secret_bytes(secret), signed, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode()
