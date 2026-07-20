"""Clerk session-JWT verification.

Uses a locally generated RSA key and a stubbed JWKS cache — no network, no Clerk
account needed. These assertions encode the security contract: RS256 only,
issuer pinned, expiry enforced, unknown keys refused.
"""
import dataclasses
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app import auth_clerk
from app.auth_clerk import AuthError, _normalize_role, principal_from_claims

ISSUER = "https://example.clerk.accounts.dev"
KID = "test-key-1"


@pytest.fixture
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def clerk_configured(monkeypatch, rsa_key):
    """Enable Clerk in settings and preload the JWKS cache with our test key."""
    cfg = dataclasses.replace(
        auth_clerk.settings,
        clerk_jwks_url="https://example.invalid/.well-known/jwks.json",
        clerk_issuer=ISSUER)
    monkeypatch.setattr(auth_clerk, "settings", cfg)
    auth_clerk.reset_cache()
    monkeypatch.setattr(auth_clerk, "_keys", {KID: rsa_key.public_key()})
    monkeypatch.setattr(auth_clerk, "_fetched_at", time.monotonic())
    yield
    auth_clerk.reset_cache()


def _token(rsa_key, *, alg="RS256", kid=KID, issuer=ISSUER, exp_delta=300, **extra):
    now = int(time.time())
    claims = {"sub": "user_abc", "iss": issuer, "iat": now,
              "exp": now + exp_delta, "sid": "sess_1", **extra}
    key = rsa_key if alg == "RS256" else "symmetric-secret"
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": kid})


async def test_valid_token_accepted(rsa_key):
    claims = await auth_clerk.verify_session_token(_token(rsa_key, org_id="org_1"))
    assert claims["sub"] == "user_abc"
    assert claims["org_id"] == "org_1"


async def test_expired_token_rejected(rsa_key):
    with pytest.raises(AuthError, match="expired"):
        await auth_clerk.verify_session_token(_token(rsa_key, exp_delta=-60))


async def test_wrong_issuer_rejected(rsa_key):
    with pytest.raises(AuthError):
        await auth_clerk.verify_session_token(_token(rsa_key, issuer="https://evil.example"))


async def test_hs256_token_rejected(rsa_key):
    """Algorithm confusion: an HMAC-signed token must never be accepted."""
    with pytest.raises(AuthError, match="algorithm"):
        await auth_clerk.verify_session_token(_token(rsa_key, alg="HS256"))


async def test_unknown_kid_rejected(rsa_key, monkeypatch):
    async def _no_refresh():
        return None
    monkeypatch.setattr(auth_clerk, "_fetch_jwks", _no_refresh)
    with pytest.raises(AuthError, match="unknown signing key"):
        await auth_clerk.verify_session_token(_token(rsa_key, kid="rotated-away"))


async def test_missing_kid_rejected(rsa_key):
    now = int(time.time())
    token = jwt.encode({"sub": "u", "iss": ISSUER, "iat": now, "exp": now + 60},
                       rsa_key, algorithm="RS256")
    with pytest.raises(AuthError, match="key id"):
        await auth_clerk.verify_session_token(token)


async def test_garbage_token_rejected():
    with pytest.raises(AuthError, match="malformed"):
        await auth_clerk.verify_session_token("not-a-jwt")


async def test_tampered_signature_rejected(rsa_key):
    token = _token(rsa_key)
    head, payload, _sig = token.split(".")
    with pytest.raises(AuthError):
        await auth_clerk.verify_session_token(f"{head}.{payload}.AAAA")


@pytest.mark.parametrize("raw,expected", [
    ("org:admin", "admin"), ("admin", "admin"), ("org:owner", "owner"),
    ("org:member", "member"), ("org:custom_thing", "member"), (None, None),
])
def test_role_normalization(raw, expected):
    assert _normalize_role(raw) == expected


def test_principal_shape():
    p = principal_from_claims({"sub": "u1", "sid": "s1", "org_id": "org_1",
                               "org_role": "org:admin", "org_slug": "acme"})
    assert p["user_id"] == "u1"
    assert p["clerk_org_id"] == "org_1"
    assert p["org_role"] == "admin"
    assert p["org_slug"] == "acme"


def test_principal_without_org():
    p = principal_from_claims({"sub": "u1"})
    assert p["clerk_org_id"] is None and p["org_role"] is None
