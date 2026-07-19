-- Conductor — multi-tenant agent-orchestration schema
-- Layers: orgs → projects → (agents | tasks | messages | locks | activity | api_keys)
--
-- ⚠ REFERENCE ONLY — NO LONGER APPLIED AT BOOT.
-- Alembic now owns the schema (server/migrations/versions). Migration
-- 0001_baseline reproduces this file plus the additive changes that used to live
-- in the in-code migrate(). Run migrations via `./run.sh migrate` (or the compose
-- `migrate` service, which `up` triggers automatically). Kept here as a readable
-- snapshot of the starting schema; edit migrations, not this file.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ─────────────────────────────────────────────────────────────
-- Tenant layer
-- ─────────────────────────────────────────────────────────────
CREATE TABLE orgs (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name        TEXT NOT NULL,
  slug        TEXT UNIQUE NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE projects (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  slug        TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, slug)
);

-- Identity: each Claude agent + the UI arrives with its own bearer token
CREATE TABLE api_keys (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  label       TEXT NOT NULL,              -- free label: 'dev-a', 'dev-b', 'ci', 'ui'
  key_hash    TEXT NOT NULL UNIQUE,       -- sha256(token) — the plain token is never stored
  role        TEXT NOT NULL DEFAULT 'agent' CHECK (role IN ('agent','ui','admin')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used   TIMESTAMPTZ
);
CREATE INDEX ON api_keys (key_hash);

-- ─────────────────────────────────────────────────────────────
-- Work layer (all isolated by project_id)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE agents (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  name            TEXT NOT NULL,          -- agent label (free), e.g. dev-a / dev-b / ci
  machine         TEXT,
  status          TEXT NOT NULL DEFAULT 'offline'
                    CHECK (status IN ('offline','idle','working','blocked')),
  current_task_id UUID,
  note            TEXT,                   -- free text: "what it's doing right now"
  last_heartbeat  TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (project_id, name)
);

CREATE TABLE tasks (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id   UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  title        TEXT NOT NULL,
  spec         TEXT,                      -- detailed task description
  status       TEXT NOT NULL DEFAULT 'todo'
                 CHECK (status IN ('todo','claimed','in_progress','blocked','review','done')),
  assign_mode  TEXT NOT NULL DEFAULT 'auto'  CHECK (assign_mode IN ('auto','manual')),
  assignee     TEXT,                      -- agent name (manual assignment or claimer)
  depends_on   UUID[] NOT NULL DEFAULT '{}',   -- can't be claimed until all deps are 'done'
  priority     INT NOT NULL DEFAULT 0,        -- higher = first
  lease_until  TIMESTAMPTZ,               -- lock duration; if it passes, task can be re-claimed (crash resistance)
  board_order  DOUBLE PRECISION NOT NULL DEFAULT 0,  -- ordering within a kanban column
  artifacts    JSONB NOT NULL DEFAULT '{}',   -- {pr_url, commit, files:[...]}
  created_by   TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON tasks (project_id, status, priority DESC, created_at);

CREATE TABLE messages (
  id          BIGSERIAL PRIMARY KEY,
  project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  from_agent  TEXT,
  to_agent    TEXT,                       -- NULL = broadcast (everyone)
  task_id     UUID,
  body        TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  read_by     TEXT[] NOT NULL DEFAULT '{}'
);
CREATE INDEX ON messages (project_id, id);

-- Advisory resource lock (so two agents don't touch the same file at once)
CREATE TABLE locks (
  project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  resource    TEXT NOT NULL,              -- e.g. 'file:services/x/y.py'
  held_by     TEXT NOT NULL,
  expires_at  TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (project_id, resource)
);

-- Append-only event log (UI feed + audit)
CREATE TABLE activity (
  id          BIGSERIAL PRIMARY KEY,
  project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  actor       TEXT,
  kind        TEXT NOT NULL,              -- task.created, task.claimed, task.done, message.posted, agent.status...
  detail      JSONB NOT NULL DEFAULT '{}',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON activity (project_id, id DESC);

-- Memory book — agents' persistent, timestamped notes/decisions/handoffs
CREATE TABLE memory (
  id          BIGSERIAL PRIMARY KEY,
  project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  author      TEXT,
  kind        TEXT NOT NULL DEFAULT 'note',   -- note | decision | handoff
  body        TEXT NOT NULL,
  task_id     UUID,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON memory (project_id, id DESC);

-- Async work: the last time each agent "synced" (for sync catch-up)
ALTER TABLE agents ADD COLUMN IF NOT EXISTS last_synced_at TIMESTAMPTZ;
