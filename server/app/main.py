"""
Conductor — standalone, multi-tenant agent-orchestration core.
Single service: bearer-auth REST API (UI + agents talk through here).
Isolation: each token is bound to one project; all queries are filtered by project_id.
In Phase 1.b the same core functions will also be wrapped as MCP tools.
"""
import asyncio
import hashlib
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import asyncpg
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request

from . import billing, db, saas
from .config import settings
from .logging_setup import get_logger, setup_logging
from .observability import init_error_tracking
from .security import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from .util import ser, sha256_hex as _hash

# NB: renamed from `log` to avoid colliding with the async activity-log helper
# `log(c, pid, ...)` defined below, which rebinds the module global `log`.
applog = get_logger("conductor.api")

# `pool` aliases the control-plane pool (assigned in lifespan from app.db).
# Existing handlers use it directly for shared-tenant data. As orgs move to
# dedicated databases (the growth switch), data-plane handlers should route via
# db.tenant_pool(org_id) instead — app.db is the only place that knows which.
pool: asyncpg.Pool | None = None


async def _resolve_token(tok: str) -> dict | None:
    """bearer token → {project_id, role, label} (None if absent). Shared by REST + MCP."""
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT project_id, role, label FROM api_keys "
            "WHERE key_hash=$1 AND revoked_at IS NULL", _hash(tok))
        if row:
            await c.execute("UPDATE api_keys SET last_used=now() WHERE key_hash=$1", _hash(tok))
    return dict(row) if row else None


async def seed():
    """Dev-only: idempotent default org + project + bootstrap admin key.
    Gated behind settings.dev_seed — the SaaS provisions workspaces on purchase
    (Phase 2), so this never runs in production (config.validate() enforces it)."""
    async with pool.acquire() as c:
        org = await c.fetchrow(
            """INSERT INTO orgs (name, slug) VALUES ($1,$2)
               ON CONFLICT (slug) DO UPDATE SET name=EXCLUDED.name RETURNING id""",
            settings.default_org, settings.default_org.lower(),
        )
        proj = await c.fetchrow(
            """INSERT INTO projects (org_id, name, slug) VALUES ($1,$2,$3)
               ON CONFLICT (org_id, slug) DO UPDATE SET name=EXCLUDED.name RETURNING id""",
            org["id"], settings.default_project, settings.default_project.lower(),
        )
        if settings.bootstrap_admin_token:
            await c.execute(
                """INSERT INTO api_keys (project_id, label, key_hash, role)
                   VALUES ($1,'bootstrap-admin',$2,'admin')
                   ON CONFLICT (key_hash) DO NOTHING""",
                proj["id"], _hash(settings.bootstrap_admin_token),
            )


# Schema is now owned by Alembic (server/migrations). Migrations run out of band
# (CI / deploy / the compose `migrate` one-shot), never per app instance — see
# migrate_all.py. The old in-code migrate() has been retired.


async def _inproc_reaper(interval_s: int):
    """LOCAL-DEV ONLY crash-recovery loop for Postgres images without pg_cron.
    In production the reaper is a pg_cron job (migration 0002) and this never
    runs — config.validate() rejects REAPER_MODE=inproc in production because it
    is unsafe with more than one instance."""
    while True:
        try:
            await asyncio.sleep(interval_s)
            async with pool.acquire() as c:
                await c.execute("SELECT conductor_reap()")
        except asyncio.CancelledError:
            break
        except Exception:  # noqa: BLE001
            applog.warning("inproc reaper tick failed", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    setup_logging()
    settings.validate()
    init_error_tracking()             # no-op unless SENTRY_DSN is set
    await db.init_pools()
    pool = db.control_pool()          # alias for existing handlers
    if settings.dev_seed:
        await seed()
    reaper_task = None
    if settings.reaper_mode == "inproc":
        reaper_task = asyncio.create_task(_inproc_reaper(settings.reaper_interval_s))
        applog.info("in-process reaper started (dev only)")
    # MCP session-manager lifespan (required for streamable-http)
    async with mcp_app.lifespan(app):
        yield
    if reaper_task:
        reaper_task.cancel()
    await db.close_pools()


app = FastAPI(title="Conductor", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,       # explicit allow-list, not "*" in prod
    allow_methods=["*"], allow_headers=["*"],
)
# Outermost so the headers land on every response, including CORS preflights.
app.add_middleware(SecurityHeadersMiddleware)
# Reject oversized bodies before anything reads them.
app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_body_bytes)


# ─────────────────────────────────────────────────────────────
# Identity: bearer token → {project_id, role, label}
# ─────────────────────────────────────────────────────────────
async def caller(authorization: str = Header(default="")) -> dict:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Bearer token required")
    row = await _resolve_token(authorization.split(" ", 1)[1].strip())
    if not row:
        raise HTTPException(401, "Invalid token")
    return row


async def require_admin(who: dict = Depends(caller)) -> dict:
    if who["role"] != "admin":
        raise HTTPException(403, "Admin privileges required")
    return who


async def log(c, pid, actor, kind, detail=None):
    await c.execute(
        "INSERT INTO activity (project_id, actor, kind, detail) VALUES ($1,$2,$3,$4)",
        pid, actor, kind, detail or {},
    )


# ─────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    """Liveness — the process is up. Cheap, no dependencies. Cloud Run uses this
    to decide whether to restart the container."""
    return {"ok": True, "service": "conductor", "version": "0.1.0"}


@app.get("/ready")
async def ready():
    """Readiness — the process can serve traffic (DB reachable). Kept separate
    from liveness so a DB blip doesn't cause pointless container restarts."""
    try:
        async with pool.acquire() as c:
            await c.fetchval("SELECT 1")
        return {"ok": True, "db": "up"}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"db down: {e}")


# ─────────────────────────────────────────────────────────────
# Agents
# ─────────────────────────────────────────────────────────────
class AgentReg(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    machine: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=1000)


@app.post("/api/agents/register")
async def register_agent(b: AgentReg, who: dict = Depends(caller)):
    async with pool.acquire() as c:
        row = await c.fetchrow(
            """INSERT INTO agents (project_id, name, machine, note, status, last_heartbeat)
               VALUES ($1,$2,$3,$4,'idle',now())
               ON CONFLICT (project_id, name)
               DO UPDATE SET machine=COALESCE(EXCLUDED.machine, agents.machine),
                             note=COALESCE(EXCLUDED.note, agents.note),
                             status='idle', last_heartbeat=now()
               RETURNING *""",
            who["project_id"], b.name, b.machine, b.note,
        )
        await log(c, who["project_id"], b.name, "agent.registered", {"machine": b.machine})
    return ser(row)


class Heartbeat(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    status: str | None = Field(default=None, max_length=20)   # idle | working | blocked
    note: str | None = Field(default=None, max_length=1000)
    current_task_id: str | None = None


@app.post("/api/agents/heartbeat")
async def heartbeat(b: Heartbeat, who: dict = Depends(caller)):
    async with pool.acquire() as c:
        row = await c.fetchrow(
            """UPDATE agents SET
                 status=COALESCE($3, status),
                 note=COALESCE($4, note),
                 current_task_id=COALESCE($5::uuid, current_task_id),
                 last_heartbeat=now()
               WHERE project_id=$1 AND name=$2 RETURNING *""",
            who["project_id"], b.name, b.status, b.note, b.current_task_id,
        )
        if not row:
            raise HTTPException(404, "Agent not found — register first")
        if row["current_task_id"]:  # alive → refresh the lease on the current task
            await c.execute(
                "UPDATE tasks SET lease_until=now()+interval '15 minutes' "
                "WHERE project_id=$1 AND id=$2 AND status IN ('claimed','in_progress')",
                who["project_id"], row["current_task_id"])
    return ser(row)


@app.get("/api/agents")
async def list_agents(who: dict = Depends(caller)):
    async with pool.acquire() as c:
        rows = await c.fetch(
            "SELECT * FROM agents WHERE project_id=$1 ORDER BY name", who["project_id"]
        )
    return [ser(r) for r in rows]


# ─────────────────────────────────────────────────────────────
# Tasks
# ─────────────────────────────────────────────────────────────
class TaskNew(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    spec: str | None = Field(default=None, max_length=50000)
    priority: int = 0
    depends_on: list[str] = Field(default=[], max_length=100)
    assign_mode: str = "auto"            # auto | manual
    assignee: str | None = Field(default=None, max_length=120)


@app.post("/api/tasks")
async def create_task(b: TaskNew, who: dict = Depends(caller)):
    async with pool.acquire() as c:
        row = await c.fetchrow(
            """INSERT INTO tasks (project_id, title, spec, priority, depends_on, assign_mode, assignee, created_by)
               VALUES ($1,$2,$3,$4,$5::uuid[],$6,$7,$8) RETURNING *""",
            who["project_id"], b.title, b.spec, b.priority, b.depends_on,
            b.assign_mode, b.assignee, who["label"],
        )
        await log(c, who["project_id"], who["label"], "task.created",
                  {"task_id": str(row["id"]), "title": b.title})
    return ser(row)


@app.get("/api/tasks")
async def list_tasks(status: str | None = None, who: dict = Depends(caller)):
    async with pool.acquire() as c:
        if status:
            rows = await c.fetch(
                """SELECT * FROM tasks WHERE project_id=$1 AND status=$2
                   ORDER BY board_order, priority DESC, created_at""",
                who["project_id"], status)
        else:
            rows = await c.fetch(
                """SELECT * FROM tasks WHERE project_id=$1
                   ORDER BY board_order, priority DESC, created_at""",
                who["project_id"])
    return [ser(r) for r in rows]


class Claim(BaseModel):
    agent: str
    lease_seconds: int = 1800


@app.post("/api/tasks/claim")
async def claim_task(b: Claim, who: dict = Depends(caller)):
    """Atomically claim the next eligible task (FOR UPDATE SKIP LOCKED).
       auto tasks can be taken by anyone; manual tasks only by the assigned agent.
       A task is skipped unless all of its dependencies are 'done'."""
    async with pool.acquire() as c:
        async with c.transaction():
            row = await c.fetchrow(
                """UPDATE tasks t SET status='claimed', assignee=$2,
                        lease_until=now() + ($3 || ' seconds')::interval, updated_at=now()
                   WHERE t.id = (
                     SELECT id FROM tasks
                     WHERE project_id=$1
                       AND status='todo'
                       AND (assign_mode='auto' OR (assign_mode='manual' AND assignee=$2))
                       AND NOT EXISTS (
                         SELECT 1 FROM tasks d
                         WHERE d.id = ANY(tasks.depends_on) AND d.status <> 'done')
                     ORDER BY priority DESC, board_order, created_at
                     FOR UPDATE SKIP LOCKED LIMIT 1)
                   RETURNING *""",
                who["project_id"], b.agent, str(b.lease_seconds),
            )
            if not row:
                return {"claimed": None}
            await c.execute(
                """UPDATE agents SET status='working', current_task_id=$3, last_heartbeat=now()
                   WHERE project_id=$1 AND name=$2""",
                who["project_id"], b.agent, row["id"])
            await log(c, who["project_id"], b.agent, "task.claimed",
                      {"task_id": str(row["id"]), "title": row["title"]})
    return {"claimed": ser(row)}


class TaskPatch(BaseModel):
    status: str | None = None
    assignee: str | None = None
    assign_mode: str | None = None
    priority: int | None = None
    board_order: float | None = None
    spec: str | None = None
    title: str | None = None
    artifacts: dict | None = None
    prompt: str | None = None            # Claude prompt embedded in the card
    prompt_state: str | None = None      # idle | pending | running | done


@app.patch("/api/tasks/{task_id}")
async def patch_task(task_id: str, b: TaskPatch, who: dict = Depends(caller)):
    """Kanban + progress update (drag-and-drop, status, assignment, artifact, prompt).
       If a prompt is provided it is appended to the history (prompt_history) — who wrote it, in which status."""
    hist_entry = None
    if b.prompt is not None:
        hist_entry = [{"prompt": b.prompt, "by": who["label"], "status": b.status or "",
                       "at": datetime.now(timezone.utc).isoformat()}]
    async with pool.acquire() as c:
        row = await c.fetchrow(
            """UPDATE tasks SET
                 status=COALESCE($3,status),
                 assignee=COALESCE($4,assignee),
                 assign_mode=COALESCE($5,assign_mode),
                 priority=COALESCE($6,priority),
                 board_order=COALESCE($7,board_order),
                 spec=COALESCE($8,spec),
                 title=COALESCE($9,title),
                 artifacts=COALESCE($10,artifacts),
                 prompt=COALESCE($11,prompt),
                 prompt_state=COALESCE($12,prompt_state),
                 prompt_history=CASE WHEN $13::jsonb IS NOT NULL
                                     THEN prompt_history || $13::jsonb ELSE prompt_history END,
                 updated_at=now()
               WHERE project_id=$1 AND id=$2::uuid RETURNING *""",
            who["project_id"], task_id, b.status, b.assignee, b.assign_mode,
            b.priority, b.board_order, b.spec, b.title, b.artifacts,
            b.prompt, b.prompt_state, hist_entry,
        )
        if not row:
            raise HTTPException(404, "Task not found")
        # When the task is finished/released, free the agent that claimed it
        if b.status in ("done", "review", "blocked", "todo"):
            await c.execute(
                """UPDATE agents SET status='idle', current_task_id=NULL, last_heartbeat=now()
                   WHERE project_id=$1 AND current_task_id=$2::uuid""",
                who["project_id"], task_id)
        await log(c, who["project_id"], who["label"], "task.updated",
                  {"task_id": task_id, "status": b.status})
    return ser(row)


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str, who: dict = Depends(caller)):
    """Permanently delete the task — clean up dependency arrays, the agent binding, and message/note references."""
    async with pool.acquire() as c:
        async with c.transaction():
            t = await c.fetchrow(
                "SELECT title FROM tasks WHERE project_id=$1 AND id=$2::uuid",
                who["project_id"], task_id)
            if not t:
                raise HTTPException(404, "Task not found")
            # remove it from other tasks' depends_on arrays (no dangling dependencies left)
            await c.execute(
                "UPDATE tasks SET depends_on=array_remove(depends_on,$2::uuid), updated_at=now() "
                "WHERE project_id=$1 AND $2::uuid = ANY(depends_on)",
                who["project_id"], task_id)
            # free the agent working on this task
            await c.execute(
                "UPDATE agents SET current_task_id=NULL, status='idle' "
                "WHERE project_id=$1 AND current_task_id=$2::uuid",
                who["project_id"], task_id)
            # loosen message/note references (so an FK, if any, doesn't block the delete)
            await c.execute("UPDATE messages SET task_id=NULL WHERE project_id=$1 AND task_id=$2::uuid",
                            who["project_id"], task_id)
            await c.execute("UPDATE memory SET task_id=NULL WHERE project_id=$1 AND task_id=$2::uuid",
                            who["project_id"], task_id)
            await c.execute("DELETE FROM tasks WHERE project_id=$1 AND id=$2::uuid",
                            who["project_id"], task_id)
            await log(c, who["project_id"], who["label"], "task.deleted",
                      {"task_id": task_id, "title": t["title"]})
    return {"deleted": task_id}


# ─────────────────────────────────────────────────────────────
# Automation: local listener endpoints (Active-triggered work queue)
#   flow: GET /api/inbox → POST /grab (atomic take) → Claude runs → POST /finish (advance)
# ─────────────────────────────────────────────────────────────
@app.get("/api/inbox")
async def inbox(wait: int = 0, who: dict = Depends(caller)):
    """Work assigned to me with a pending prompt. SHORT-POLL: returns immediately.

    The old long-poll held the request (and a DB connection) open for up to ~55s.
    That is incompatible with a scale-to-zero compute tier — a sleeping instance
    can't hold connections and holding CPU defeats the cost model. Listeners
    should instead poll every few seconds; each call is cheap and wakes the
    service on demand. `wait` is accepted for backward compatibility and ignored.

    Active(claimed)=first pass · Test(in_progress)=fix · Review=smoke test."""
    me = who["label"]
    async with pool.acquire() as c:
        rows = await c.fetch(
            """SELECT * FROM tasks WHERE project_id=$1 AND assignee=$2
                 AND prompt_state='pending' AND status IN ('claimed','in_progress','review')
               ORDER BY board_order, priority DESC, created_at""",
            who["project_id"], me)
    return {"agent": me, "tasks": [ser(r) for r in rows], "poll_after_s": 3}


class GrabIn(BaseModel):
    lease_seconds: int = 1800

@app.post("/api/tasks/{task_id}/grab")
async def grab_task(task_id: str, b: GrabIn, who: dict = Depends(caller)):
    """ATOMICALLY grab the prompt (pending→running + lease) — so a second poll doesn't run it twice.
       Only the person the task is assigned to can grab it. Returns: {grabbed:true,task} | {grabbed:false}."""
    async with pool.acquire() as c:
        row = await c.fetchrow(
            """UPDATE tasks SET prompt_state='running',
                 prompt_lease=now()+($3||' seconds')::interval, updated_at=now()
               WHERE project_id=$1 AND id=$2::uuid AND assignee=$4 AND prompt_state='pending'
               RETURNING *""",
            who["project_id"], task_id, str(b.lease_seconds), who["label"])
        if not row:
            return {"grabbed": False}
        await c.execute(
            "UPDATE agents SET status='working', current_task_id=$3, last_heartbeat=now() "
            "WHERE project_id=$1 AND name=$2", who["project_id"], who["label"], task_id)
        await log(c, who["project_id"], who["label"], "task.grabbed",
                  {"task_id": task_id, "status": row["status"]})
    return {"grabbed": True, "task": ser(row)}


class FinishIn(BaseModel):
    ok: bool = True
    result: str | None = None
    artifacts: dict | None = None

@app.post("/api/tasks/{task_id}/finish")
async def finish_task(task_id: str, b: FinishIn, who: dict = Depends(caller)):
    """Complete the prompt work, advance the card along the lifecycle + write the result to the history.
       Active(claimed)→Test(in_progress) · Test→Test (fix) · Review→ok?Done:Test."""
    async with pool.acquire() as c:
        cur = await c.fetchrow(
            "SELECT status FROM tasks WHERE project_id=$1 AND id=$2::uuid",
            who["project_id"], task_id)
        if not cur:
            raise HTTPException(404, "Task not found")
        st = cur["status"]
        if st == "claimed":
            new_status = "in_progress"          # first pass done → Test
        elif st == "in_progress":
            new_status = "in_progress"          # fix done → stay in Test
        elif st == "review":
            new_status = "done" if b.ok else "in_progress"   # smoke passed→Done / failed→Test
        else:
            new_status = st
        entry = [{"kind": "result", "by": who["label"], "ok": b.ok,
                  "result": (b.result or "")[:4000], "from_status": st,
                  "at": datetime.now(timezone.utc).isoformat()}]
        art = b.artifacts if b.artifacts is not None else {}
        row = await c.fetchrow(
            """UPDATE tasks SET status=$3, prompt_state='done', prompt_lease=NULL,
                 prompt_history=prompt_history || $5::jsonb,
                 artifacts=CASE WHEN $4::jsonb <> '{}'::jsonb THEN artifacts || $4 ELSE artifacts END,
                 updated_at=now()
               WHERE project_id=$1 AND id=$2::uuid RETURNING *""",
            who["project_id"], task_id, new_status, art, entry)
        await c.execute(
            "UPDATE agents SET status='idle', current_task_id=NULL, last_heartbeat=now() "
            "WHERE project_id=$1 AND current_task_id=$2::uuid", who["project_id"], task_id)
        await log(c, who["project_id"], who["label"], "task.finished",
                  {"task_id": task_id, "from": st, "to": new_status, "ok": b.ok})
    return ser(row)


# ─────────────────────────────────────────────────────────────
# Messaging (agent-to-agent + UI feed)
# ─────────────────────────────────────────────────────────────
class MsgNew(BaseModel):
    body: str = Field(min_length=1, max_length=8000)
    to_agent: str | None = None          # NULL = broadcast
    task_id: str | None = None
    from_agent: str | None = None


@app.post("/api/messages")
async def post_message(b: MsgNew, who: dict = Depends(caller)):
    async with pool.acquire() as c:
        row = await c.fetchrow(
            """INSERT INTO messages (project_id, from_agent, to_agent, task_id, body)
               VALUES ($1,$2,$3,$4::uuid,$5) RETURNING *""",
            who["project_id"], b.from_agent or who["label"], b.to_agent, b.task_id, b.body,
        )
        await log(c, who["project_id"], b.from_agent or who["label"], "message.posted",
                  {"to": b.to_agent, "task_id": b.task_id})
    return ser(row)


@app.get("/api/messages")
async def poll_messages(since: int = 0, to: str | None = None, limit: int = 200,
                        who: dict = Depends(caller)):
    """Messages after `since` (message id). If `to` is provided: private-to-me + broadcast."""
    limit = min(max(limit, 1), 500)      # never let a caller request an unbounded page
    async with pool.acquire() as c:
        if to:
            rows = await c.fetch(
                """SELECT * FROM messages WHERE project_id=$1 AND id>$2
                   AND (to_agent IS NULL OR to_agent=$3)
                   ORDER BY id LIMIT $4""",
                who["project_id"], since, to, limit)
        else:
            rows = await c.fetch(
                """SELECT * FROM messages WHERE project_id=$1 AND id>$2
                   ORDER BY id LIMIT $3""",
                who["project_id"], since, limit)
    return [ser(r) for r in rows]


# ─────────────────────────────────────────────────────────────
# Live output stream (watch a teammate's Claude in real time)
#   producer: an agent appends chunks of its own work (listener or emit tool)
#   consumer: anyone in the project tails an agent with ?agent=&since=
#   Rows are disposable — conductor_reap() prunes them after a day.
# ─────────────────────────────────────────────────────────────
_STREAM_KINDS = ("text", "tool", "result", "sys")


class StreamIn(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    kind: str = "text"                   # text | tool | result | sys
    task_id: str | None = None


@app.post("/api/stream")
async def push_stream(b: StreamIn, who: dict = Depends(caller)):
    """Append a chunk of live output for the CALLING agent (agent = token label).
    kind: text (assistant tokens) | tool (a command) | result (an outcome) | sys."""
    if b.kind not in _STREAM_KINDS:
        raise HTTPException(400, f"kind must be one of {_STREAM_KINDS}")
    async with pool.acquire() as c:
        row = await c.fetchrow(
            """INSERT INTO stream_events (project_id, agent, task_id, kind, content)
               VALUES ($1,$2,$3::uuid,$4,$5) RETURNING id""",
            who["project_id"], who["label"], b.task_id, b.kind, b.content[:8000])
    return {"id": row["id"]}


@app.get("/api/stream")
async def read_stream(agent: str, since: int = 0, limit: int = 500,
                      who: dict = Depends(caller)):
    """Tail a teammate's live output. Returns a bare array of
    {id, kind, content, task_id, created_at} with id>since, oldest first."""
    lim = min(max(limit, 1), 1000)
    async with pool.acquire() as c:
        rows = await c.fetch(
            """SELECT id, kind, content, task_id, created_at FROM stream_events
               WHERE project_id=$1 AND agent=$2 AND id>$3 ORDER BY id LIMIT $4""",
            who["project_id"], agent, since, lim)
    return [ser(r) for r in rows]


# ─────────────────────────────────────────────────────────────
# Advisory locks (prevent double-touching the same file)
# ─────────────────────────────────────────────────────────────
class LockReq(BaseModel):
    resource: str
    held_by: str
    seconds: int = 900


@app.post("/api/locks")
async def acquire_lock(b: LockReq, who: dict = Depends(caller)):
    async with pool.acquire() as c:
        row = await c.fetchrow(
            """INSERT INTO locks (project_id, resource, held_by, expires_at)
               VALUES ($1,$2,$3, now() + ($4 || ' seconds')::interval)
               ON CONFLICT (project_id, resource) DO UPDATE
                 SET held_by=EXCLUDED.held_by, expires_at=EXCLUDED.expires_at
                 WHERE locks.expires_at < now() OR locks.held_by=EXCLUDED.held_by
               RETURNING *""",
            who["project_id"], b.resource, b.held_by, str(b.seconds),
        )
        if not row:  # held by someone else, not yet expired
            cur = await c.fetchrow(
                "SELECT held_by, expires_at FROM locks WHERE project_id=$1 AND resource=$2",
                who["project_id"], b.resource)
            return {"acquired": False, "held_by": cur["held_by"], "expires_at": str(cur["expires_at"])}
    return {"acquired": True, **ser(row)}


@app.delete("/api/locks/{resource:path}")
async def release_lock(resource: str, held_by: str, who: dict = Depends(caller)):
    async with pool.acquire() as c:
        res = await c.execute(
            "DELETE FROM locks WHERE project_id=$1 AND resource=$2 AND held_by=$3",
            who["project_id"], resource, held_by)
    return {"released": res.endswith("1")}


# ─────────────────────────────────────────────────────────────
# Aggregate state for the UI (polling; SSE in Phase 1.5)
# ─────────────────────────────────────────────────────────────
# Board presence — which token-labels currently have the board open. Persisted in
# the `presence` table (migration 0002) instead of an in-process dict, so it's
# correct across replicas and survives cold starts.
_PRESENCE_WINDOW_S = 12


@app.get("/api/state")
async def state(who: dict = Depends(caller)):
    pid = who["project_id"]
    async with pool.acquire() as c:
        await c.execute(
            """INSERT INTO presence (project_id, label, role, last_seen)
               VALUES ($1,$2,$3, now())
               ON CONFLICT (project_id, label)
               DO UPDATE SET last_seen=now(), role=EXCLUDED.role""",
            pid, who["label"], who["role"])
        agents = await c.fetch("SELECT * FROM agents WHERE project_id=$1 ORDER BY name", pid)
        tasks = await c.fetch(
            """SELECT * FROM tasks WHERE project_id=$1
               ORDER BY board_order, priority DESC, created_at""", pid)
        counts = await c.fetch(
            "SELECT status, count(*) FROM tasks WHERE project_id=$1 GROUP BY status", pid)
        acts = await c.fetch(
            "SELECT * FROM activity WHERE project_id=$1 ORDER BY id DESC LIMIT 50", pid)
        msgs = await c.fetch(
            "SELECT * FROM messages WHERE project_id=$1 ORDER BY id DESC LIMIT 40", pid)
        prow = await c.fetchrow("SELECT name, brief FROM projects WHERE id=$1", pid)
        users = await c.fetch(
            """SELECT k.label, k.role,
                      COALESCE(p.last_seen > now() - ($2 || ' seconds')::interval, false) AS online
               FROM (SELECT DISTINCT label, role FROM api_keys WHERE project_id=$1) k
               LEFT JOIN presence p ON p.project_id=$1 AND p.label=k.label
               ORDER BY k.label""",
            pid, str(_PRESENCE_WINDOW_S))
    return {
        "project": prow["name"],
        "brief": prow["brief"],
        "agents": [ser(a) for a in agents],
        "tasks": [ser(t) for t in tasks],
        "counts": {r["status"]: r["count"] for r in counts},
        "activity": [ser(a) for a in acts],
        "messages": [ser(m) for m in msgs],
        "server_time": datetime.now(timezone.utc).isoformat(),
        "users": [ser(u) for u in users],
    }


# ─────────────────────────────────────────────────────────────
# Admin: mint a new agent key + open a new tenant (project)
# ─────────────────────────────────────────────────────────────
class KeyNew(BaseModel):
    label: str
    role: str = "agent"


@app.post("/api/admin/keys")
async def mint_key(b: KeyNew, request: Request, who: dict = Depends(require_admin)):
    # We return the plaintext token once; only its hash is stored in the DB.
    raw = hashlib.sha256(os.urandom(32)).hexdigest()
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO api_keys (project_id, label, key_hash, role) VALUES ($1,$2,$3,$4)",
            who["project_id"], b.label, _hash(raw), b.role)
    return {"label": b.label, "role": b.role, "token": raw,
            "note": "This token is shown only now — save it."}


class TenantNew(BaseModel):
    org: str
    project: str


@app.post("/api/admin/tenants")
async def new_tenant(b: TenantNew, who: dict = Depends(require_admin)):
    async with pool.acquire() as c:
        org = await c.fetchrow(
            """INSERT INTO orgs (name, slug) VALUES ($1,$2)
               ON CONFLICT (slug) DO UPDATE SET name=EXCLUDED.name RETURNING id""",
            b.org, b.org.lower())
        proj = await c.fetchrow(
            """INSERT INTO projects (org_id, name, slug) VALUES ($1,$2,$3)
               ON CONFLICT (org_id, slug) DO UPDATE SET name=EXCLUDED.name RETURNING *""",
            org["id"], b.project, b.project.lower())
    return ser(proj)


# ─────────────────────────────────────────────────────────────
# Memory ledger + full timestamped journal (Memory page)
# ─────────────────────────────────────────────────────────────
class MemoryNew(BaseModel):
    body: str = Field(min_length=1, max_length=50000)
    kind: str = "note"                   # note | decision | handoff
    task_id: str | None = None
    author: str | None = None


@app.post("/api/memory")
async def add_memory(b: MemoryNew, who: dict = Depends(caller)):
    async with pool.acquire() as c:
        row = await c.fetchrow(
            """INSERT INTO memory (project_id, author, kind, body, task_id)
               VALUES ($1,$2,$3,$4,$5::uuid) RETURNING *""",
            who["project_id"], b.author or who["label"], b.kind, b.body, b.task_id)
        await log(c, who["project_id"], b.author or who["label"], "memory.added", {"kind": b.kind})
    return ser(row)


@app.get("/api/journal")
async def journal(limit: int = 300, who: dict = Depends(caller)):
    """Memory page: activity + messages + memory notes — broad history, the client merges them."""
    pid = who["project_id"]; lim = min(max(limit, 1), 1000)
    async with pool.acquire() as c:
        acts = await c.fetch("SELECT * FROM activity WHERE project_id=$1 ORDER BY id DESC LIMIT $2", pid, lim)
        msgs = await c.fetch("SELECT * FROM messages WHERE project_id=$1 ORDER BY id DESC LIMIT $2", pid, lim)
        mems = await c.fetch("SELECT * FROM memory WHERE project_id=$1 ORDER BY id DESC LIMIT $2", pid, lim)
    return {"activity": [ser(a) for a in acts], "messages": [ser(m) for m in msgs],
            "memory": [ser(m) for m in mems], "server_time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/analytics")
async def analytics(who: dict = Depends(caller)):
    """Analytics page: by agent, by hour (local), by day, task status, totals."""
    pid = who["project_id"]; tz = "Europe/Istanbul"
    async with pool.acquire() as c:
        per_agent = await c.fetch(
            "SELECT actor, count(*) n FROM activity WHERE project_id=$1 AND actor IS NOT NULL "
            "GROUP BY actor ORDER BY n DESC", pid)
        by_hour = await c.fetch(
            f"SELECT EXTRACT(HOUR FROM created_at AT TIME ZONE '{tz}')::int hr, count(*) n "
            "FROM activity WHERE project_id=$1 GROUP BY hr ORDER BY hr", pid)
        by_day = await c.fetch(
            f"SELECT to_char(date_trunc('day', created_at AT TIME ZONE '{tz}'),'YYYY-MM-DD') d, count(*) n "
            "FROM activity WHERE project_id=$1 AND created_at > now()-interval '14 days' GROUP BY d ORDER BY d", pid)
        tstatus = await c.fetch("SELECT status, count(*) n FROM tasks WHERE project_id=$1 GROUP BY status", pid)
        kinds = await c.fetch("SELECT kind, count(*) n FROM activity WHERE project_id=$1 GROUP BY kind ORDER BY n DESC", pid)
        totals = await c.fetchrow(
            """SELECT (SELECT count(*) FROM activity WHERE project_id=$1) events,
                      (SELECT count(*) FROM messages WHERE project_id=$1) messages,
                      (SELECT count(*) FROM memory   WHERE project_id=$1) memory,
                      (SELECT count(*) FROM tasks    WHERE project_id=$1 AND status='done') tasks_done,
                      (SELECT count(DISTINCT actor) FROM activity WHERE project_id=$1) contributors""", pid)
    return {
        "per_agent": [{"agent": r["actor"], "n": r["n"]} for r in per_agent],
        "by_hour": [{"hr": r["hr"], "n": r["n"]} for r in by_hour],
        "by_day": [{"d": r["d"], "n": r["n"]} for r in by_day],
        "tasks_status": {r["status"]: r["n"] for r in tstatus},
        "kinds": [{"kind": r["kind"], "n": r["n"]} for r in kinds],
        "totals": dict(totals),
    }


# ─────────────────────────────────────────────────────────────
# Project brief (working directive embedded in the server — environment, git flow, rules)
# ─────────────────────────────────────────────────────────────
class BriefIn(BaseModel):
    brief: str


@app.get("/api/project")
async def get_project(who: dict = Depends(caller)):
    async with pool.acquire() as c:
        row = await c.fetchrow("SELECT name, brief FROM projects WHERE id=$1", who["project_id"])
    return ser(row)


@app.patch("/api/project/brief")
async def set_brief(b: BriefIn, who: dict = Depends(require_admin)):
    async with pool.acquire() as c:
        await c.execute("UPDATE projects SET brief=$2 WHERE id=$1", who["project_id"], b.brief)
        await log(c, who["project_id"], who["label"], "project.brief_updated", {})
    return {"ok": True}


# ─────────────────────────────────────────────────────────────
# MCP layer — the same core's tools for Claude Code agents.
# Streamable-HTTP; identity comes from the bearer token's LABEL (e.g. dev-a / dev-b / ci),
# so an agent cannot impersonate someone else. Each tool runs isolated by project_id.
# ─────────────────────────────────────────────────────────────
mcp = FastMCP("Conductor")


async def _mcp_ctx() -> dict:
    """The MCP request's bearer token → {project_id, role, label}. label = agent identity."""
    req = get_http_request()
    authz = req.headers.get("authorization", "")
    if not authz.lower().startswith("bearer "):
        raise ValueError("Authorization: Bearer <token> required (.mcp.json headers)")
    row = await _resolve_token(authz.split(" ", 1)[1].strip())
    if not row:
        raise ValueError("Invalid Conductor token")
    return row


@mcp.tool
async def whoami() -> dict:
    """Returns who you are, which project you are in, and the PROJECT BRIEF (working directive:
    environment, git flow, rules). Call this FIRST when you connect and follow the brief."""
    ctx = await _mcp_ctx()
    async with pool.acquire() as c:
        brief = await c.fetchval("SELECT brief FROM projects WHERE id=$1", ctx["project_id"])
    return {"agent": ctx["label"], "role": ctx["role"],
            "project_id": str(ctx["project_id"]), "brief": brief}


@mcp.tool
async def sync() -> dict:
    """A live snapshot of the board + ASYNC CATCH-UP. Because everyone works at different hours:
    call it when you start working → returns team status, your open tasks, your unread messages,
    'what happened since you were last here' (since_last_visit), and the latest handoff notes
    (recent_handoffs). This call also updates your 'I'm here' marker."""
    ctx = await _mcp_ctx(); pid = ctx["project_id"]; me = ctx["label"]
    async with pool.acquire() as c:
        prev = await c.fetchval(
            "SELECT last_synced_at FROM agents WHERE project_id=$1 AND name=$2", pid, me)
        agents = await c.fetch(
            "SELECT name,status,note,current_task_id,last_heartbeat FROM agents WHERE project_id=$1 ORDER BY name", pid)
        counts = await c.fetch(
            "SELECT status,count(*) FROM tasks WHERE project_id=$1 GROUP BY status", pid)
        mine = await c.fetch(
            """SELECT id,title,status,priority FROM tasks
               WHERE project_id=$1 AND assignee=$2 AND status NOT IN ('done')
               ORDER BY priority DESC, created_at""", pid, me)
        unread = await c.fetch(
            """SELECT id,from_agent,to_agent,body,created_at FROM messages
               WHERE project_id=$1 AND (to_agent IS NULL OR to_agent=$2)
               AND NOT ($2 = ANY(read_by)) ORDER BY id DESC LIMIT 15""", pid, me)
        away = []
        if prev:
            away = await c.fetch(
                """SELECT actor,kind,detail,created_at FROM activity
                   WHERE project_id=$1 AND created_at>$2 AND actor IS NOT NULL AND actor<>$3
                   ORDER BY id DESC LIMIT 40""", pid, prev, me)
        handoffs = await c.fetch(
            "SELECT author,body,created_at FROM memory WHERE project_id=$1 AND kind='handoff' ORDER BY id DESC LIMIT 5", pid)
        await c.execute("UPDATE agents SET last_synced_at=now() WHERE project_id=$1 AND name=$2", pid, me)
        brief = await c.fetchval("SELECT brief FROM projects WHERE id=$1", pid)
    return {"you": me, "brief": brief, "team": [ser(a) for a in agents],
            "counts": {r["status"]: r["count"] for r in counts},
            "your_open_tasks": [ser(t) for t in mine],
            "unread_messages": [ser(m) for m in unread],
            "since_last_visit": [ser(a) for a in away],
            "recent_handoffs": [ser(h) for h in handoffs]}


@mcp.tool
async def remember(body: str, kind: str = "note", task_id: str = "") -> dict:
    """Write a permanent, timestamped note to the memory ledger (attributed to you, visible to everyone).
    kind: note (general info) | decision (a decision made) | handoff (handover).
    CRITICAL FOR ASYNC WORK: when you stop working, write a 'what I did + what to do next' summary
    as kind='handoff'; the next person to arrive sees it in recent_handoffs via sync()."""
    ctx = await _mcp_ctx()
    async with pool.acquire() as c:
        row = await c.fetchrow(
            """INSERT INTO memory (project_id, author, kind, body, task_id)
               VALUES ($1,$2,$3,$4,$5::uuid) RETURNING *""",
            ctx["project_id"], ctx["label"], kind, body, task_id or None)
        await log(c, ctx["project_id"], ctx["label"], "memory.added", {"kind": kind})
    return ser(row)


@mcp.tool
async def register(machine: str = "", note: str = "", branch: str = "") -> dict:
    """Register/refresh yourself as an agent (idle) + report the git BRANCH you are working on.
    Call it once at the start of a session (branch = your own git branch, e.g. feature/my-feature)."""
    ctx = await _mcp_ctx()
    async with pool.acquire() as c:
        row = await c.fetchrow(
            """INSERT INTO agents (project_id,name,machine,note,branch,status,last_heartbeat)
               VALUES ($1,$2,$3,$4,$5,'idle',now())
               ON CONFLICT (project_id,name) DO UPDATE SET
                 machine=COALESCE(NULLIF(EXCLUDED.machine,''),agents.machine),
                 note=COALESCE(NULLIF(EXCLUDED.note,''),agents.note),
                 branch=COALESCE(NULLIF(EXCLUDED.branch,''),agents.branch),
                 status='idle', last_heartbeat=now() RETURNING *""",
            ctx["project_id"], ctx["label"], machine, note, branch or None)
        await log(c, ctx["project_id"], ctx["label"], "agent.registered",
                  {"machine": machine, "branch": branch})
    return ser(row)


@mcp.tool
async def heartbeat(status: str = "working", note: str = "", branch: str = "") -> dict:
    """Report that you are alive + what you are doing (+ optional branch). status: idle|working|blocked."""
    ctx = await _mcp_ctx()
    async with pool.acquire() as c:
        row = await c.fetchrow(
            """UPDATE agents SET status=$3, note=COALESCE(NULLIF($4,''),note),
                 branch=COALESCE(NULLIF($5,''),branch), last_heartbeat=now()
               WHERE project_id=$1 AND name=$2 RETURNING *""",
            ctx["project_id"], ctx["label"], status, note, branch or None)
        if row and row["current_task_id"]:  # alive → refresh the lease on the current task
            await c.execute(
                "UPDATE tasks SET lease_until=now()+interval '15 minutes' "
                "WHERE project_id=$1 AND id=$2 AND status IN ('claimed','in_progress')",
                ctx["project_id"], row["current_task_id"])
    return ser(row) if row else {"error": "call register first"}


@mcp.tool
async def report_git(branch: str = "", ahead: int = 0, behind: int = 0,
                     conflicts: int = 0, dirty: bool = False, note: str = "") -> dict:
    """Report git status to the board: branch name, how many commits AHEAD / BEHIND the remote,
    number of unresolved conflicts, whether the working tree is dirty. Call it after push/pull/merge or
    when a conflict comes up — everyone sees it on the board, which prevents clashes across branches."""
    ctx = await _mcp_ctx()
    git = {"branch": branch, "ahead": ahead, "behind": behind, "conflicts": conflicts,
           "dirty": dirty, "note": note, "at": datetime.now(timezone.utc).isoformat()}
    async with pool.acquire() as c:
        await c.execute(
            "UPDATE agents SET git=$3, branch=COALESCE(NULLIF($4,''),branch), last_heartbeat=now() "
            "WHERE project_id=$1 AND name=$2", ctx["project_id"], ctx["label"], git, branch or None)
        await log(c, ctx["project_id"], ctx["label"], "git.reported",
                  {"branch": branch, "ahead": ahead, "behind": behind, "conflicts": conflicts})
    return {"ok": True, "git": git}


@mcp.tool
async def create_task(title: str, spec: str = "", priority: int = 0,
                      depends_on: list[str] | None = None, assign_mode: str = "auto",
                      assignee: str = "") -> dict:
    """Create a new task. assign_mode: auto (pulled from the pool) / manual (locked to the assignee).
    depends_on: ids of tasks that must be finished before this task can start."""
    ctx = await _mcp_ctx()
    async with pool.acquire() as c:
        row = await c.fetchrow(
            """INSERT INTO tasks (project_id,title,spec,priority,depends_on,assign_mode,assignee,created_by)
               VALUES ($1,$2,$3,$4,$5::uuid[],$6,$7,$8) RETURNING *""",
            ctx["project_id"], title, spec or None, priority, depends_on or [],
            assign_mode, assignee or None, ctx["label"])
        await log(c, ctx["project_id"], ctx["label"], "task.created",
                  {"task_id": str(row["id"]), "title": title})
    return ser(row)


@mcp.tool
async def claim_next_task(lease_seconds: int = 1800) -> dict:
    """ATOMICALLY claim the next eligible task (SKIP LOCKED — two agents never take the same task).
    Tasks with unfinished dependencies or manually assigned to someone else are skipped.
    Returns: {claimed:{...}} or {claimed:null}."""
    ctx = await _mcp_ctx(); me = ctx["label"]
    async with pool.acquire() as c:
        async with c.transaction():
            row = await c.fetchrow(
                """UPDATE tasks t SET status='claimed', assignee=$2,
                        lease_until=now()+($3||' seconds')::interval, updated_at=now()
                   WHERE t.id=(SELECT id FROM tasks WHERE project_id=$1 AND status='todo'
                       AND (assign_mode='auto' OR (assign_mode='manual' AND assignee=$2))
                       AND NOT EXISTS (SELECT 1 FROM tasks d
                           WHERE d.id=ANY(tasks.depends_on) AND d.status<>'done')
                       ORDER BY priority DESC, board_order, created_at
                       FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *""",
                ctx["project_id"], me, str(lease_seconds))
            if not row:
                return {"claimed": None,
                        "hint": "No eligible task — check the board with sync() or add one with create_task."}
            await c.execute(
                "UPDATE agents SET status='working', current_task_id=$3, last_heartbeat=now() "
                "WHERE project_id=$1 AND name=$2", ctx["project_id"], me, row["id"])
            await log(c, ctx["project_id"], me, "task.claimed",
                      {"task_id": str(row["id"]), "title": row["title"]})
    return {"claimed": ser(row)}


@mcp.tool
async def update_task(task_id: str, status: str = "", note: str = "",
                      artifacts: dict | None = None) -> dict:
    """Update a task's status/output. status: in_progress|blocked|review|done|todo.
    When the work is done, status='done'. artifacts e.g.: {"pr":"...","commit":"...","files":[...]}."""
    ctx = await _mcp_ctx()
    art = artifacts or {}
    async with pool.acquire() as c:
        row = await c.fetchrow(
            """UPDATE tasks SET status=COALESCE(NULLIF($3,''),status),
                 artifacts=CASE WHEN $4::jsonb <> '{}'::jsonb THEN $4 ELSE artifacts END,
                 updated_at=now()
               WHERE project_id=$1 AND id=$2::uuid RETURNING *""",
            ctx["project_id"], task_id, status, art)
        if not row:
            return {"error": "task not found"}
        if status in ("done", "review", "blocked", "todo"):
            await c.execute(
                "UPDATE agents SET status='idle', current_task_id=NULL "
                "WHERE project_id=$1 AND current_task_id=$2::uuid", ctx["project_id"], task_id)
        await log(c, ctx["project_id"], ctx["label"], "task.updated",
                  {"task_id": task_id, "status": status})
    return ser(row)


@mcp.tool
async def post_message(body: str, to_agent: str = "", task_id: str = "") -> dict:
    """Write a message to the team. If to_agent is empty, it goes to everyone (broadcast)."""
    ctx = await _mcp_ctx()
    async with pool.acquire() as c:
        row = await c.fetchrow(
            """INSERT INTO messages (project_id,from_agent,to_agent,task_id,body)
               VALUES ($1,$2,$3,$4::uuid,$5) RETURNING *""",
            ctx["project_id"], ctx["label"], to_agent or None, task_id or None, body)
        await log(c, ctx["project_id"], ctx["label"], "message.posted", {"to": to_agent or "all"})
    return ser(row)


@mcp.tool
async def emit(content: str, kind: str = "text", task_id: str = "") -> dict:
    """Stream a chunk of what you're doing to the live task terminal, so teammates
    can watch you work in real time. Call it as you go on a claimed task.
    kind: text (your narration) | tool (a command you ran) | result (an outcome) | sys."""
    ctx = await _mcp_ctx()
    k = kind if kind in _STREAM_KINDS else "text"
    async with pool.acquire() as c:
        row = await c.fetchrow(
            """INSERT INTO stream_events (project_id, agent, task_id, kind, content)
               VALUES ($1,$2,$3::uuid,$4,$5) RETURNING id""",
            ctx["project_id"], ctx["label"], task_id or None, k, content[:8000])
    return {"id": row["id"]}


@mcp.tool
async def read_messages() -> dict:
    """Fetch your unread messages (private + broadcast) and mark them as read."""
    ctx = await _mcp_ctx(); me = ctx["label"]; pid = ctx["project_id"]
    async with pool.acquire() as c:
        rows = await c.fetch(
            """SELECT * FROM messages WHERE project_id=$1 AND (to_agent IS NULL OR to_agent=$2)
               AND NOT ($2 = ANY(read_by)) ORDER BY id""", pid, me)
        if rows:
            await c.execute(
                "UPDATE messages SET read_by=array_append(read_by,$1) "
                "WHERE project_id=$2 AND id=ANY($3::bigint[]) AND NOT ($1=ANY(read_by))",
                me, pid, [r["id"] for r in rows])
    return {"messages": [ser(r) for r in rows]}


@mcp.tool
async def acquire_file_lock(resource: str, seconds: int = 900) -> dict:
    """Acquire an advisory lock on a file/resource (so another agent doesn't edit it at the same time).
    resource e.g.: "file:services/x/y.py". Returns {acquired:true} or {acquired:false,held_by}."""
    ctx = await _mcp_ctx()
    async with pool.acquire() as c:
        row = await c.fetchrow(
            """INSERT INTO locks (project_id,resource,held_by,expires_at)
               VALUES ($1,$2,$3, now()+($4||' seconds')::interval)
               ON CONFLICT (project_id,resource) DO UPDATE
                 SET held_by=EXCLUDED.held_by, expires_at=EXCLUDED.expires_at
                 WHERE locks.expires_at<now() OR locks.held_by=EXCLUDED.held_by RETURNING *""",
            ctx["project_id"], resource, ctx["label"], str(seconds))
        if not row:
            cur = await c.fetchrow(
                "SELECT held_by,expires_at FROM locks WHERE project_id=$1 AND resource=$2",
                ctx["project_id"], resource)
            return {"acquired": False, "held_by": cur["held_by"], "expires_at": str(cur["expires_at"])}
    return {"acquired": True, "resource": resource}


@mcp.tool
async def release_file_lock(resource: str) -> dict:
    """Release the advisory lock you hold."""
    ctx = await _mcp_ctx()
    async with pool.acquire() as c:
        res = await c.execute(
            "DELETE FROM locks WHERE project_id=$1 AND resource=$2 AND held_by=$3",
            ctx["project_id"], resource, ctx["label"])
    return {"released": res.endswith("1")}


mcp_app = mcp.http_app(path="/")


# ─────────────────────────────────────────────────────────────
# Mount order: /health + /api defined first (route, wins) →
# /mcp (streamable-http) → / (UI static, catch-all last).
# ─────────────────────────────────────────────────────────────
# /mcp (without slash) → /mcp/ 307 redirect (so a client can connect even if it gives the URL without a slash;
# 307 preserves method+body → MCP POST/GET/DELETE isn't broken). Defined BEFORE the mount → catches the exact "/mcp".
from starlette.responses import RedirectResponse  # noqa: E402

# Dashboard (Clerk-authed human API), billing, and provider webhooks. Registered
# before the static catch-all mount so "/" never swallows them.
app.include_router(saas.router)
app.include_router(saas.webhooks)
app.include_router(billing.router)
app.include_router(billing.webhooks)


@app.api_route("/mcp", methods=["GET", "POST", "DELETE", "OPTIONS"])
async def _mcp_slash_redirect():
    return RedirectResponse(url="/mcp/", status_code=307)


app.mount("/mcp", mcp_app)

from fastapi.staticfiles import StaticFiles  # noqa: E402
from starlette.exceptions import HTTPException as StarletteHTTPException  # noqa: E402


class SPAStaticFiles(StaticFiles):
    """Serve the built SPA, falling back to index.html for client-side routes.

    Without this, a deep link like /board would 404 — the file doesn't exist on
    disk, the router owns that path. API routes are registered before this mount,
    so they always win.
    """

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


_here = os.path.dirname(__file__)
_SPA = os.path.join(_here, "..", "static")     # built frontend (web/ → vite build)
_LEGACY_UI = os.path.join(_here, "..", "ui")   # original single-file board

if os.path.isdir(_SPA) and os.path.isfile(os.path.join(_SPA, "index.html")):
    app.mount("/", SPAStaticFiles(directory=_SPA, html=True), name="spa")
elif os.path.isdir(_LEGACY_UI):
    # Fallback so a checkout without a frontend build still serves a board.
    app.mount("/", StaticFiles(directory=_LEGACY_UI, html=True), name="ui")
