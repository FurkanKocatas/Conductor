"""Shared request dependencies for the human-facing API.

Lives apart from saas.py and billing.py so both can use these without importing
each other (saas needs billing's quota helpers; billing needs these auth
dependencies — a direct cycle otherwise).

Identity flow: Clerk session JWT → principal → resolved Conductor org → role gate.
"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException

from . import db
from .auth_clerk import AuthError, principal_from_claims, verify_session_token
from .config import settings
from .logging_setup import get_logger

log = get_logger("conductor.deps")

# Statuses that still allow a user to reach billing (so they can subscribe or
# fix payment) but not to use the product.
_USABLE = {"active"}
_REACHABLE = {"active", "pending", "suspended"}


async def clerk_principal(authorization: str = Header(default="")) -> dict:
    """Verify the Clerk session JWT and return the caller's identity."""
    if not settings.clerk_enabled:
        raise HTTPException(501, "Dashboard auth is not configured (Clerk disabled)")
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Bearer token required")
    try:
        claims = await verify_session_token(authorization.split(" ", 1)[1].strip())
    except AuthError as e:
        raise HTTPException(401, str(e))
    principal = principal_from_claims(claims)
    if not principal["user_id"]:
        raise HTTPException(401, "Token has no subject")
    return principal


async def resolve_org(clerk_org_id: str, slug: str | None) -> dict:
    """Map a Clerk organization to its Conductor org, creating it if absent.

    The initial status is the on-switch: with billing configured a new org is
    'pending' and unusable until Stripe reports an active subscription. Without
    billing (self-hosted / dev) it is immediately 'active'.
    """
    initial_status = "pending" if settings.billing_enabled else "active"
    async with db.control_pool().acquire() as c:
        row = await c.fetchrow(
            """SELECT id, name, slug, status, plan, clerk_org_id, stripe_customer_id
               FROM orgs WHERE clerk_org_id=$1""", clerk_org_id)
        if row:
            return dict(row)
        row = await c.fetchrow(
            """INSERT INTO orgs (name, slug, clerk_org_id, status)
               VALUES ($1,$2,$3,$4)
               ON CONFLICT (slug) DO UPDATE SET clerk_org_id=EXCLUDED.clerk_org_id
               RETURNING id, name, slug, status, plan, clerk_org_id, stripe_customer_id""",
            slug or clerk_org_id, clerk_org_id, clerk_org_id, initial_status)
        log.info("provisioned org", extra={"ctx": {
            "clerk_org_id": clerk_org_id, "status": initial_status}})
        return dict(row)


async def _org_ctx(principal: dict, *, usable_only: bool) -> dict:
    if not principal["clerk_org_id"]:
        raise HTTPException(403, "Select an organization before using the dashboard")
    org = await resolve_org(principal["clerk_org_id"], principal.get("org_slug"))
    status = org["status"]
    if status not in _REACHABLE:
        raise HTTPException(403, "This workspace is no longer available")
    if usable_only and status not in _USABLE:
        # 402 tells the dashboard to send the user to checkout / billing portal.
        raise HTTPException(402, {
            "pending": "No active subscription — subscribe to activate this workspace.",
            "suspended": "Subscription inactive — update billing to restore access.",
        }.get(status, "Workspace is not active"))
    return {**principal, "org": org}


async def org_context(principal: dict = Depends(clerk_principal)) -> dict:
    """Full access. Requires an ACTIVE (paid) workspace."""
    return await _org_ctx(principal, usable_only=True)


async def billing_context(principal: dict = Depends(clerk_principal)) -> dict:
    """Billing routes only. Reachable while pending/suspended — otherwise a user
    could never subscribe or fix a failed payment."""
    return await _org_ctx(principal, usable_only=False)


def require_roles(*allowed: str):
    """Dependency factory enforcing the org role carried by the Clerk JWT."""
    async def _dep(ctx: dict = Depends(org_context)) -> dict:
        if ctx.get("org_role") not in allowed:
            raise HTTPException(403, f"Requires one of: {', '.join(allowed)}")
        return ctx
    return _dep


def require_billing_roles(*allowed: str):
    """Same, but on the billing (inactive-tolerant) context."""
    async def _dep(ctx: dict = Depends(billing_context)) -> dict:
        if ctx.get("org_role") not in allowed:
            raise HTTPException(403, f"Requires one of: {', '.join(allowed)}")
        return ctx
    return _dep


admin_context = require_roles("owner", "admin")
billing_admin_context = require_billing_roles("owner", "admin")


async def owned_project(project_id: str, org_id) -> dict:
    """Fetch a project, enforcing that it belongs to the caller's org."""
    async with db.control_pool().acquire() as c:
        row = await c.fetchrow(
            "SELECT id, name, slug, org_id FROM projects WHERE id=$1::uuid AND org_id=$2",
            project_id, org_id)
    if not row:
        raise HTTPException(404, "Project not found")
    return dict(row)
