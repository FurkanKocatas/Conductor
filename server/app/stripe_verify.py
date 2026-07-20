"""Stripe webhook signature verification.

Deliberately NOT the same as svix_verify: Stripe's scheme differs in three ways
that are easy to get wrong.

  header:          Stripe-Signature: t=<unix>,v1=<hex>[,v1=<hex>...][,v0=...]
  signed_payload:  f"{t}.{raw_body}"
  digest:          HMAC-SHA256 rendered as HEX (Svix uses base64)
  secret:          used as the raw UTF-8 string, INCLUDING the "whsec_" prefix
                   (Svix base64-decodes the part after the prefix)

Multiple v1 entries are accepted so secret rotation works. v0 is Stripe's older
test-mode scheme and is ignored.
"""
from __future__ import annotations

import hashlib
import hmac
import time


class StripeWebhookError(Exception):
    """Signature or timestamp verification failed."""


DEFAULT_TOLERANCE_S = 300


def _parse_header(header: str) -> tuple[int, list[str]]:
    timestamp: int | None = None
    signatures: list[str] = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError as e:
                raise StripeWebhookError("invalid timestamp in signature header") from e
        elif key == "v1" and value:
            signatures.append(value)
    if timestamp is None:
        raise StripeWebhookError("signature header has no timestamp")
    if not signatures:
        raise StripeWebhookError("signature header has no v1 signature")
    return timestamp, signatures


def verify(body: bytes, header: str | None, secret: str,
           tolerance_s: int = DEFAULT_TOLERANCE_S, now: float | None = None) -> None:
    """Raise StripeWebhookError unless `body` carries a valid, fresh signature."""
    if not secret:
        raise StripeWebhookError("stripe webhook secret is not configured")
    if not header:
        raise StripeWebhookError("missing Stripe-Signature header")

    timestamp, signatures = _parse_header(header)

    current = time.time() if now is None else now
    if abs(current - timestamp) > tolerance_s:
        raise StripeWebhookError("webhook timestamp outside tolerance")

    signed = f"{timestamp}.".encode() + body
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()

    for candidate in signatures:
        if hmac.compare_digest(candidate, expected):   # constant-time
            return
    raise StripeWebhookError("no matching signature")


def sign(body: bytes, timestamp: int, secret: str) -> str:
    """Build a Stripe-Signature header value. Test helper."""
    signed = f"{timestamp}.".encode() + body
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"
