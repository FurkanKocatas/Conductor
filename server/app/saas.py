"""Dashboard (human) API + Clerk webhooks — the SaaS control plane.

Separate from main.py on purpose: main.py serves AGENTS (project-scoped bearer
tokens, MCP tools, the board API). This module serves PEOPLE, authenticated by a
Clerk session JWT, and is where orgs/projects/API keys are managed.

Tenancy: the Clerk JWT carries the user's active organization. That maps to
exactly one Conductor org, and every query here is constrained to it — a user can
never read or mutate another org's projects or keys.

All tables touched here (orgs, projects, api_keys) are CONTROL-plane, so they use
db.control_pool() directly rather than the tenant router.
"""
from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from . import db
from .auth_clerk import AuthError, principal_from_claims, verify_session_token
from .config import settings
from .logging_setup import get_logger
from .svix_verify import WebhookError, verify as verify_svix
from .util import ser, sha256_hex

log = get_logger("conductor.saas")

router = APIRouter(prefix="/api/dash", tags=["dashboard"])
webhooks = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


# ─────────────────────────────────────────────────────────────
# Identity: Clerk session JWT → principal → org context
# ─────────────────────────────────────────────────────────────
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


async def _resolve_org(clerk_org_id: str, slug: str | None) -> dict:
    """Map a Clerk organization to its Conductor org, creating it if absent.

    Phase 1 provisions lazily so the dashboard works as soon as Clerk is wired
    up. Phase 2 moves creation behind the Stripe 'subscription active' webhook
    and this function becomes lookup-only for unknown orgs.
    """
    async with db.control_pool().acquire() as c:
        row = await c.fetchrow(
            "SELECT id, name, slug, status, plan, clerk_org_id FROM orgs WHERE clerk_org_id=$1",
            clerk_org_id)
        if row:
            return dict(row)
        row = await c.fetchrow(
            """INSERT INTO orgs (name, slug, clerk_org_id)
               VALUES ($1, $2, $3)
               ON CONFLICT (slug) DO UPDATE SET clerk_org_id=EXCLUDED.clerk_org_id
               RETURNING id, name, slug, status, plan, clerk_org_id""",
            slug or clerk_org_id, clerk_org_id, clerk_org_id)
        log.info("provisioned org", extra={"ctx": {"clerk_org_id": clerk_org_id}})
        return dict(row)


async def org_context(principal: dict = Depends(clerk_principal)) -> dict:
    """Principal + resolved org. Rejects users with no active organization and
    workspaces that aren't active (Phase 2 sets status on billing events)."""
    if not principal["clerk_org_id"]:
        raise HTTPException(403, "Select an organization before using the dashboard")
    org = await _resolve_org(principal["clerk_org_id"], principal.get("org_slug"))
    if org["status"] != "active":
        raise HTTPException(402, "Workspace is inactive — an active subscription is required")
    return {**principal, "org": org}


def require_roles(*allowed: str):
    """Dependency factory enforcing the org role from the Clerk JWT."""
    async def _dep(ctx: dict = Depends(org_context)) -> dict:
        if ctx.get("org_role") not in allowed:
            raise HTTPException(403, f"Requires one of: {', '.join(allowed)}")
        return ctx
    return _dep


admin_context = require_roles("owner", "admin")


async def _owned_project(project_id: str, org_id) -> dict:
    """Fetch a project, enforcing that it belongs to the caller's org."""
    async with db.control_pool().acquire() as c:
        row = await c.fetchrow(
            "SELECT id, name, slug, org_id FROM projects WHERE id=$1::uuid AND org_id=$2",
            project_id, org_id)
    if not row:
        raise HTTPException(404, "Project not found")
    return dict(row)


# ─────────────────────────────────────────────────────────────
# Dashboard endpoints
# ─────────────────────────────────────────────────────────────
@router.get("/me")
async def me(ctx: dict = Depends(org_context)):
    """Who am I, which workspace am I in, and what can I do."""
    async with db.control_pool().acquire() as c:
        projects = await c.fetch(
            "SELECT id, name, slug, created_at FROM projects WHERE org_id=$1 ORDER BY name",
            ctx["org"]["id"])
    return {
        "user_id": ctx["user_id"],
        "email": ctx["email"],
        "role": ctx["org_role"],
        "org": ser(ctx["org"]),
        "projects": [ser(p) for p in projects],
    }


@router.get("/projects")
async def list_projects(ctx: dict = Depends(org_context)):
    async with db.control_pool().acquire() as c:
        rows = await c.fetch(
            "SELECT id, name, slug, created_at, created_by FROM projects "
            "WHERE org_id=$1 ORDER BY name", ctx["org"]["id"])
    return [ser(r) for r in rows]


class ProjectNew(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@router.post("/projects", status_code=201)
async def create_project(b: ProjectNew, ctx: dict = Depends(admin_context)):
    slug = _slugify(b.name)
    async with db.control_pool().acquire() as c:
        row = await c.fetchrow(
            """INSERT INTO projects (org_id, name, slug, created_by)
               VALUES ($1,$2,$3,$4)
               ON CONFLICT (org_id, slug) DO NOTHING
               RETURNING id, name, slug, created_at, created_by""",
            ctx["org"]["id"], b.name, slug, ctx["user_id"])
    if not row:
        raise HTTPException(409, "A project with that name already exists")
    return ser(row)


@router.get("/projects/{project_id}/keys")
async def list_keys(project_id: str, ctx: dict = Depends(org_context)):
    """Key metadata only — the secret is unrecoverable by design."""
    project = await _owned_project(project_id, ctx["org"]["id"])
    async with db.control_pool().acquire() as c:
        rows = await c.fetch(
            """SELECT id, label, role, created_at, last_used, revoked_at, created_by
               FROM api_keys WHERE project_id=$1 ORDER BY created_at DESC""",
            project["id"])
    return [ser(r) for r in rows]


class KeyNew(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    role: str = "agent"


@router.post("/projects/{project_id}/keys", status_code=201)
async def mint_key(project_id: str, b: KeyNew, ctx: dict = Depends(admin_context)):
    """Mint an agent token for a project. Returned in plaintext exactly once."""
    if b.role not in {"agent", "ui"}:
        raise HTTPException(400, "role must be 'agent' or 'ui'")
    project = await _owned_project(project_id, ctx["org"]["id"])
    raw = os.urandom(32).hex()
    async with db.control_pool().acquire() as c:
        row = await c.fetchrow(
            """INSERT INTO api_keys (project_id, label, key_hash, role, created_by)
               VALUES ($1,$2,$3,$4,$5) RETURNING id, label, role, created_at""",
            project["id"], b.label, sha256_hex(raw), b.role, ctx["user_id"])
    return {**ser(row), "token": raw,
            "note": "This token is shown only once — store it now."}


@router.delete("/keys/{key_id}")
async def revoke_key(key_id: str, ctx: dict = Depends(admin_context)):
    """Soft-revoke: the row survives for audit, but the token stops working."""
    async with db.control_pool().acquire() as c:
        row = await c.fetchrow(
            """UPDATE api_keys k SET revoked_at=now()
               FROM projects p
               WHERE k.id=$1::uuid AND k.project_id=p.id AND p.org_id=$2
                 AND k.revoked_at IS NULL
               RETURNING k.id""",
            key_id, ctx["org"]["id"])
    if not row:
        raise HTTPException(404, "Key not found or already revoked")
    return {"revoked": key_id}


def _slugify(name: str) -> str:
    """URL-safe ASCII slug. Non-ASCII letters become separators rather than being
    passed through, so slugs stay predictable in URLs and unique per org."""
    out = "".join(ch.lower() if (ch.isalnum() and ch.isascii()) else "-"
                  for ch in name).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out or "project"


# ─────────────────────────────────────────────────────────────
# Clerk webhooks — keep orgs in sync with Clerk
# ─────────────────────────────────────────────────────────────
@webhooks.post("/clerk")
async def clerk_webhook(request: Request):
    """Signature-verified Clerk events. Handlers are idempotent so Svix retries
    (and out-of-order redelivery) are safe."""
    if not settings.clerk_webhook_secret:
        raise HTTPException(501, "Clerk webhooks are not configured")
    body = await request.body()
    try:
        verify_svix(body, request.headers, settings.clerk_webhook_secret)
    except WebhookError as e:
        log.warning("clerk webhook rejected", extra={"ctx": {"reason": str(e)}})
        raise HTTPException(400, f"Invalid webhook signature: {e}")

    event = json.loads(body or b"{}")
    etype, data = event.get("type", ""), event.get("data", {}) or {}
    clerk_org_id = data.get("id")

    if etype in ("organization.created", "organization.updated") and clerk_org_id:
        name = data.get("name") or data.get("slug") or clerk_org_id
        slug = data.get("slug") or clerk_org_id
        async with db.control_pool().acquire() as c:
            await c.execute(
                """INSERT INTO orgs (name, slug, clerk_org_id) VALUES ($1,$2,$3)
                   ON CONFLICT (slug) DO UPDATE
                     SET name=EXCLUDED.name, clerk_org_id=EXCLUDED.clerk_org_id""",
                name, slug, clerk_org_id)
        log.info("org synced", extra={"ctx": {"type": etype, "clerk_org_id": clerk_org_id}})

    elif etype == "organization.deleted" and clerk_org_id:
        # Soft-delete: hard DELETE would cascade away every task/message. Data is
        # retained; access is refused by org_context's status check.
        async with db.control_pool().acquire() as c:
            await c.execute(
                "UPDATE orgs SET status='deleted' WHERE clerk_org_id=$1", clerk_org_id)
        log.info("org soft-deleted", extra={"ctx": {"clerk_org_id": clerk_org_id}})

    return {"received": True, "type": etype}
