"""Stripe billing — the on-switch for a workspace.

There is no free tier. A Clerk org starts 'pending' and is unusable; when Stripe
reports an active subscription the org flips to 'active' and its first project is
provisioned. Cancellation or a failed payment suspends it (data retained).

Stripe is called over plain HTTPS with httpx rather than the SDK: only two
endpoints are needed (checkout session, billing portal session), the SDK is sync
and heavy, and a lean dependency set keeps scale-to-zero cold starts fast.

Webhook handling is idempotent — every event id is recorded in `billing_events`
and replays become no-ops, because Stripe retries and can deliver out of order.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from . import db
from .config import settings
from .deps import billing_admin_context, billing_context
from .logging_setup import get_logger
from .stripe_verify import StripeWebhookError
from .stripe_verify import verify as verify_stripe

log = get_logger("conductor.billing")

router = APIRouter(prefix="/api/dash/billing", tags=["billing"])
webhooks = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

STRIPE_API = "https://api.stripe.com/v1"

# Per-plan limits. Enforced on the resources a user can create from the
# dashboard. (Agent/task metering is deliberately out of the MVP — it needs a
# usage pipeline, not just a row count.)
PLANS: dict[str, dict[str, int]] = {
    "pro": {"max_projects": 10, "max_keys_per_project": 20},
}
UNLIMITED = {"max_projects": 10**6, "max_keys_per_project": 10**6}

# Stripe subscription statuses that should keep a workspace usable.
_ACTIVE_SUB_STATES = {"active", "trialing"}


def plan_limits(plan: str | None) -> dict[str, int]:
    """Limits for a plan. Self-hosted installs (billing off) are unlimited."""
    if not settings.billing_enabled:
        return UNLIMITED
    return PLANS.get(plan or "", PLANS[settings.billing_plan_name])


# ─────────────────────────────────────────────────────────────
# Quota enforcement (called from the dashboard API)
# ─────────────────────────────────────────────────────────────
async def enforce_project_quota(org: dict) -> None:
    limits = plan_limits(org.get("plan"))
    async with db.control_pool().acquire() as c:
        n = await c.fetchval("SELECT count(*) FROM projects WHERE org_id=$1", org["id"])
    if n >= limits["max_projects"]:
        raise HTTPException(
            402, f"Plan limit reached: {limits['max_projects']} projects. Upgrade to add more.")


async def enforce_key_quota(org: dict, project_id) -> None:
    limits = plan_limits(org.get("plan"))
    async with db.control_pool().acquire() as c:
        n = await c.fetchval(
            "SELECT count(*) FROM api_keys WHERE project_id=$1 AND revoked_at IS NULL",
            project_id)
    if n >= limits["max_keys_per_project"]:
        raise HTTPException(
            402, f"Plan limit reached: {limits['max_keys_per_project']} active keys "
                 "for this project. Revoke one or upgrade.")


# ─────────────────────────────────────────────────────────────
# Minimal Stripe REST client
# ─────────────────────────────────────────────────────────────
async def _stripe_post(path: str, form: dict[str, str],
                       idempotency_key: str | None = None) -> dict:
    if not settings.stripe_secret_key:
        raise HTTPException(501, "Billing is not configured")
    headers = {"Authorization": f"Bearer {settings.stripe_secret_key}"}
    if idempotency_key:
        # Protects against double-charging if a request is retried.
        headers["Idempotency-Key"] = idempotency_key
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(f"{STRIPE_API}{path}", data=form, headers=headers)
    if resp.status_code >= 400:
        # Surface Stripe's message to logs, never the secret key.
        detail = resp.json().get("error", {}).get("message", "Stripe request failed")
        log.error("stripe error", extra={"ctx": {"path": path, "status": resp.status_code,
                                                 "detail": detail}})
        raise HTTPException(502, f"Billing provider error: {detail}")
    return resp.json()


# ─────────────────────────────────────────────────────────────
# Dashboard billing endpoints
# ─────────────────────────────────────────────────────────────
@router.get("")
async def billing_status(ctx: dict = Depends(billing_context)):
    """Current subscription state — drives the dashboard's billing screen."""
    org = ctx["org"]
    async with db.control_pool().acquire() as c:
        row = await c.fetchrow(
            """SELECT status, plan, current_period_end, provisioned_at,
                      (stripe_customer_id IS NOT NULL) AS has_customer
               FROM orgs WHERE id=$1""", org["id"])
    limits = plan_limits(row["plan"])
    return {
        "status": row["status"],
        "plan": row["plan"],
        "current_period_end": row["current_period_end"].isoformat()
                              if row["current_period_end"] else None,
        "provisioned_at": row["provisioned_at"].isoformat() if row["provisioned_at"] else None,
        "has_customer": row["has_customer"],
        "billing_enabled": settings.billing_enabled,
        "limits": limits,
    }


@router.post("/checkout")
async def create_checkout(ctx: dict = Depends(billing_admin_context)):
    """Start a subscription. Returns a Stripe Checkout URL to redirect to."""
    if not settings.billing_enabled:
        raise HTTPException(501, "Billing is not configured")
    org = ctx["org"]
    org_id = str(org["id"])
    form = {
        "mode": "subscription",
        "line_items[0][price]": settings.stripe_price_id,
        "line_items[0][quantity]": "1",
        "success_url": f"{settings.public_base_url}/?billing=success",
        "cancel_url": f"{settings.public_base_url}/?billing=cancelled",
        # Both are set so the webhook can resolve the org from either the
        # checkout session or the subscription object.
        "client_reference_id": org_id,
        "metadata[org_id]": org_id,
        "subscription_data[metadata][org_id]": org_id,
    }
    if org.get("stripe_customer_id"):
        form["customer"] = org["stripe_customer_id"]
    elif ctx.get("email"):
        form["customer_email"] = ctx["email"]

    session = await _stripe_post("/checkout/sessions", form,
                                 idempotency_key=f"checkout:{org_id}:{ctx['user_id']}")
    return {"url": session["url"]}


@router.post("/portal")
async def create_portal(ctx: dict = Depends(billing_admin_context)):
    """Open Stripe's customer portal for self-serve plan/payment management."""
    if not settings.billing_enabled:
        raise HTTPException(501, "Billing is not configured")
    customer = ctx["org"].get("stripe_customer_id")
    if not customer:
        raise HTTPException(409, "No billing account yet — subscribe first")
    session = await _stripe_post("/billing_portal/sessions", {
        "customer": customer,
        "return_url": f"{settings.public_base_url}/",
    })
    return {"url": session["url"]}


# ─────────────────────────────────────────────────────────────
# Provisioning / suspension
# ─────────────────────────────────────────────────────────────
async def _activate(org_id, *, subscription_id: str | None,
                    customer_id: str | None, period_end: int | None) -> None:
    """Flip a workspace on and make sure it has something to work with."""
    ends = (datetime.fromtimestamp(period_end, tz=timezone.utc)
            if period_end else None)
    # org_id may arrive as a str (Stripe metadata) or a UUID (DB lookup), so
    # every statement casts explicitly rather than relying on asyncpg inference.
    oid = str(org_id)
    async with db.control_pool().acquire() as c:
        async with c.transaction():
            await c.execute(
                """UPDATE orgs SET status='active', plan=$2,
                     stripe_subscription_id=COALESCE($3, stripe_subscription_id),
                     stripe_customer_id=COALESCE($4, stripe_customer_id),
                     current_period_end=COALESCE($5, current_period_end),
                     provisioned_at=COALESCE(provisioned_at, now())
                   WHERE id=$1::uuid""",
                oid, settings.billing_plan_name, subscription_id, customer_id, ends)
            # First activation: give them a project to point an agent at.
            has_project = await c.fetchval(
                "SELECT EXISTS(SELECT 1 FROM projects WHERE org_id=$1::uuid)", oid)
            if not has_project:
                await c.execute(
                    """INSERT INTO projects (org_id, name, slug)
                       VALUES ($1::uuid,'Default','default')
                       ON CONFLICT (org_id, slug) DO NOTHING""", oid)
    log.info("workspace activated", extra={"ctx": {"org_id": str(org_id)}})


async def _suspend(org_id, reason: str) -> None:
    async with db.control_pool().acquire() as c:
        await c.execute(
            "UPDATE orgs SET status='suspended' WHERE id=$1::uuid AND status<>'deleted'",
            str(org_id))
    log.info("workspace suspended", extra={"ctx": {"org_id": str(org_id), "reason": reason}})


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


async def _org_for_event(obj: dict) -> str | None:
    """Resolve the org an event belongs to: metadata first, then the customer."""
    meta = obj.get("metadata") or {}
    org_id = meta.get("org_id") or obj.get("client_reference_id")
    if org_id:
        return str(org_id)
    customer = obj.get("customer")
    if customer:
        async with db.control_pool().acquire() as c:
            found = await c.fetchval(
                "SELECT id FROM orgs WHERE stripe_customer_id=$1", customer)
        return str(found) if found else None
    return None


# ─────────────────────────────────────────────────────────────
# Stripe webhook
# ─────────────────────────────────────────────────────────────
@webhooks.post("/stripe")
async def stripe_webhook(request: Request):
    """Signature-verified, idempotent Stripe events. This is what actually turns
    a paying customer's workspace on and off."""
    if not settings.stripe_webhook_secret:
        raise HTTPException(501, "Stripe webhooks are not configured")
    body = await request.body()
    try:
        verify_stripe(body, request.headers.get("stripe-signature"),
                      settings.stripe_webhook_secret)
    except StripeWebhookError as e:
        log.warning("stripe webhook rejected", extra={"ctx": {"reason": str(e)}})
        raise HTTPException(400, f"Invalid webhook signature: {e}")

    event = json.loads(body or b"{}")
    event_id, etype = event.get("id"), event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}
    if not event_id:
        raise HTTPException(400, "Event has no id")

    org_id = await _org_for_event(obj)
    if org_id is not None and not _is_uuid(org_id):
        # Metadata is set by us, but never let a malformed value reach a uuid
        # column and turn into a 500 (which Stripe would then retry forever).
        log.warning("stripe event with non-uuid org id", extra={"ctx": {"type": etype}})
        org_id = None

    # Idempotency gate: claim the event id first. A duplicate delivery inserts
    # nothing and returns immediately, so handlers never run twice.
    async with db.control_pool().acquire() as c:
        claimed = await c.fetchval(
            """INSERT INTO billing_events (event_id, type, org_id)
               VALUES ($1,$2,$3::uuid) ON CONFLICT (event_id) DO NOTHING
               RETURNING event_id""",
            event_id, etype, str(org_id) if org_id else None)
    if not claimed:
        log.info("duplicate stripe event ignored", extra={"ctx": {"event_id": event_id}})
        return {"received": True, "duplicate": True}

    if not org_id:
        # Nothing actionable (e.g. an event for a customer we don't know).
        log.warning("stripe event without org", extra={"ctx": {"type": etype}})
        return {"received": True, "matched": False}

    if etype == "checkout.session.completed":
        await _activate(org_id, subscription_id=obj.get("subscription"),
                        customer_id=obj.get("customer"), period_end=None)

    elif etype in ("customer.subscription.created", "customer.subscription.updated"):
        if obj.get("status") in _ACTIVE_SUB_STATES:
            await _activate(org_id, subscription_id=obj.get("id"),
                            customer_id=obj.get("customer"),
                            period_end=obj.get("current_period_end"))
        else:
            await _suspend(org_id, f"subscription {obj.get('status')}")

    elif etype == "customer.subscription.deleted":
        await _suspend(org_id, "subscription deleted")

    elif etype == "invoice.payment_failed":
        await _suspend(org_id, "payment failed")

    return {"received": True, "type": etype}
