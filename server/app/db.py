"""
Database access + the tenant router.

THE SEAM
--------
Every data-plane query goes through `tenant_pool(org_id)` instead of a single
global pool. Today the `tenant_databases` registry is empty, so every org
resolves to the one shared pool. When an org outgrows shared, we insert a row
mapping its id to a dedicated Neon connection string; the router then hands back
a pool for *that* database. No query in the app changes — this module is the
only place that knows whether an org is shared or dedicated.

CONTROL PLANE vs DATA PLANE
---------------------------
- Control plane (orgs, projects, api_keys, billing, the registry itself) always
  lives in the shared/control database → `control_pool()`.
- Data plane (agents, tasks, messages, locks, activity, memory, presence) lives
  in the tenant's database → `tenant_pool(org_id)` (== control pool until split).
"""
from __future__ import annotations

import asyncio
import json

import asyncpg

from .config import settings
from .logging_setup import get_logger

log = get_logger("conductor.db")

_control_pool: asyncpg.Pool | None = None
_tenant_pools: dict[str, asyncpg.Pool] = {}   # org_id -> pool (dedicated tenants)
_org_dsn_cache: dict[str, str | None] = {}     # org_id -> dsn (None = shared)
_lock = asyncio.Lock()


async def _init_conn(conn: asyncpg.Connection) -> None:
    # jsonb/json columns cross the wire as Python objects.
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads,
                              schema="pg_catalog")
    await conn.set_type_codec("json", encoder=json.dumps, decoder=json.loads,
                              schema="pg_catalog")


async def _make_pool(dsn: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn, min_size=settings.db_pool_min, max_size=settings.db_pool_max,
        init=_init_conn,
        # A single query may not hold a pooled connection forever. Without this a
        # hung/slow statement pins a connection and, with a small pool, quickly
        # starves every other request. statement_timeout is the server-side
        # backstop; command_timeout is the client-side one.
        command_timeout=30,
        server_settings={"statement_timeout": "30000",
                         "idle_in_transaction_session_timeout": "15000"})


async def init_pools(retries: int = 30) -> None:
    """Open the control pool, retrying while the DB comes up (compose/Cloud SQL)."""
    global _control_pool
    last_err: Exception | None = None
    for _ in range(retries):
        try:
            _control_pool = await _make_pool(settings.database_url)
            log.info("control pool ready")
            return
        except Exception as e:  # noqa: BLE001
            last_err = e
            await asyncio.sleep(1)
    raise RuntimeError(f"could not connect to control DB: {last_err}")


async def close_pools() -> None:
    global _control_pool
    for p in _tenant_pools.values():
        await p.close()
    _tenant_pools.clear()
    _org_dsn_cache.clear()
    if _control_pool is not None:
        await _control_pool.close()
        _control_pool = None


def control_pool() -> asyncpg.Pool:
    if _control_pool is None:
        raise RuntimeError("control pool not initialized")
    return _control_pool


async def _dedicated_dsn(org_id: str) -> str | None:
    """Look up (and cache) an org's dedicated DSN. None → org lives on shared."""
    if org_id in _org_dsn_cache:
        return _org_dsn_cache[org_id]
    dsn: str | None = None
    async with control_pool().acquire() as c:
        # Registry is created by migration 0002. Guard so Phase-0 boots even if
        # it hasn't been applied yet.
        exists = await c.fetchval("SELECT to_regclass('public.tenant_databases')")
        if exists:
            dsn = await c.fetchval(
                "SELECT dsn FROM tenant_databases WHERE org_id=$1 AND active", org_id)
    _org_dsn_cache[org_id] = dsn
    return dsn


async def tenant_pool(org_id: str | None) -> asyncpg.Pool:
    """Return the pool holding `org_id`'s data. Shared today; dedicated once the
    registry says so. `org_id=None` (e.g. background jobs) → control pool."""
    if not org_id:
        return control_pool()
    dsn = await _dedicated_dsn(str(org_id))
    if dsn is None:
        return control_pool()
    if org_id in _tenant_pools:
        return _tenant_pools[org_id]
    async with _lock:
        if org_id not in _tenant_pools:            # double-check under lock
            _tenant_pools[org_id] = await _make_pool(dsn)
            log.info("opened dedicated pool", extra={"ctx": {"org_id": str(org_id)}})
    return _tenant_pools[org_id]


def invalidate_org(org_id: str) -> None:
    """Drop cached DSN/pool for an org after it is migrated to a dedicated DB."""
    _org_dsn_cache.pop(org_id, None)
