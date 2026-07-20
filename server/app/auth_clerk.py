"""Clerk session-JWT verification (human / dashboard auth).

Two distinct identities exist in Conductor:

  • AGENTS authenticate with a long-lived, project-scoped bearer token whose
    SHA-256 hash lives in `api_keys` (see main.caller). Unchanged.
  • HUMANS authenticate with a short-lived Clerk session JWT (RS256), verified
    here against Clerk's published JWKS. The JWT carries the user's *active
    organization*, which is the tenant boundary.

Security notes:
  - RS256 only. Never accept `alg: none`, and never allow an HMAC algorithm
    (that would let a caller sign tokens with the public key).
  - Issuer is checked against CLERK_ISSUER; expiry/not-before are enforced.
  - Clerk does not set `aud` on default session tokens, so audience validation
    is disabled deliberately rather than left to chance.
  - Unknown `kid` triggers at most one JWKS refresh (rotation support) and is
    rate-limited so a bogus-kid flood can't hammer Clerk.
  - Tokens are never logged.
"""
from __future__ import annotations

import asyncio
import json
import time

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

from .config import settings
from .logging_setup import get_logger

log = get_logger("conductor.auth")

_JWKS_TTL_S = 600.0          # re-fetch keys at most every 10 min in steady state
_REFRESH_COOLDOWN_S = 30.0   # min gap between forced refreshes (unknown kid)

_keys: dict[str, object] = {}     # kid -> public key object
_fetched_at: float = 0.0
_last_forced: float = 0.0
_lock = asyncio.Lock()


class AuthError(Exception):
    """Raised on any verification failure. Callers map this to HTTP 401."""


async def _fetch_jwks() -> None:
    """Load Clerk's JWKS and rebuild the kid -> key map."""
    global _keys, _fetched_at
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(settings.clerk_jwks_url)
        resp.raise_for_status()
        doc = resp.json()
    keys: dict[str, object] = {}
    for jwk in doc.get("keys", []):
        if jwk.get("kty") != "RSA":
            continue                       # only RSA signing keys are expected
        kid = jwk.get("kid")
        if not kid:
            continue
        keys[kid] = RSAAlgorithm.from_jwk(json.dumps(jwk))
    if not keys:
        raise AuthError("Clerk JWKS contained no usable RSA keys")
    _keys, _fetched_at = keys, time.monotonic()
    log.info("clerk jwks loaded", extra={"ctx": {"keys": len(keys)}})


async def _key_for(kid: str):
    """Return the public key for `kid`, refreshing the JWKS if needed."""
    global _last_forced
    async with _lock:
        stale = (time.monotonic() - _fetched_at) > _JWKS_TTL_S
        if not _keys or stale:
            await _fetch_jwks()
        if kid in _keys:
            return _keys[kid]
        # Unknown kid → possible key rotation. Force one refresh, rate-limited.
        if (time.monotonic() - _last_forced) > _REFRESH_COOLDOWN_S:
            _last_forced = time.monotonic()
            await _fetch_jwks()
    if kid not in _keys:
        raise AuthError("unknown signing key")
    return _keys[kid]


async def verify_session_token(token: str) -> dict:
    """Verify a Clerk session JWT and return its claims. Raises AuthError."""
    if not settings.clerk_enabled:
        raise AuthError("Clerk is not configured")
    try:
        header = jwt.get_unverified_header(token)
    except Exception as e:  # noqa: BLE001
        raise AuthError("malformed token") from e
    if header.get("alg") != "RS256":
        raise AuthError("unsupported token algorithm")
    kid = header.get("kid")
    if not kid:
        raise AuthError("token has no key id")

    key = await _key_for(kid)
    try:
        claims = jwt.decode(
            token,
            key=key,
            algorithms=["RS256"],            # pinned: no alg confusion
            issuer=settings.clerk_issuer,
            leeway=10,                       # small clock skew tolerance
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iss": True,
                "verify_aud": False,         # Clerk sets no aud by default
                "require": ["exp", "iat", "iss", "sub"],
            },
        )
    except jwt.ExpiredSignatureError as e:
        raise AuthError("token expired") from e
    except jwt.InvalidTokenError as e:
        raise AuthError("invalid token") from e
    return claims


def principal_from_claims(claims: dict) -> dict:
    """Normalize Clerk claims into the identity the app works with.

    `org_id` / `org_role` are present only when the user has an active
    organization selected. Requests without one cannot address a workspace.
    """
    return {
        "user_id": claims.get("sub"),
        "session_id": claims.get("sid"),
        "clerk_org_id": claims.get("org_id"),
        "org_role": _normalize_role(claims.get("org_role")),
        "org_slug": claims.get("org_slug"),
        "email": claims.get("email") or claims.get("primary_email_address"),
    }


def _normalize_role(role: str | None) -> str | None:
    """Clerk emits roles like 'org:admin' / 'admin' / 'org:member'. Reduce to
    our three-level model: owner | admin | member."""
    if not role:
        return None
    r = role.split(":")[-1].strip().lower()
    if r in {"owner", "admin", "member"}:
        return r
    # Unknown custom role → least privilege.
    return "member"


def reset_cache() -> None:
    """Test hook — drop cached keys."""
    global _keys, _fetched_at, _last_forced
    _keys, _fetched_at, _last_forced = {}, 0.0, 0.0
