-- Conductor — çok-kiracılı (multi-tenant) ajan-orkestrasyon şeması
-- Katman: orgs → projects → (agents | tasks | messages | locks | activity | api_keys)
--
-- ⚠ REFERENCE ONLY — NO LONGER APPLIED AT BOOT.
-- Alembic now owns the schema (server/migrations/versions). Migration
-- 0001_baseline reproduces this file plus the additive changes that used to live
-- in the in-code migrate(). Run migrations via `./run.sh migrate` (or the compose
-- `migrate` service, which `up` triggers automatically). Kept here as a readable
-- snapshot of the starting schema; edit migrations, not this file.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ─────────────────────────────────────────────────────────────
-- Kiracı katmanı
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

-- Kimlik: her Claude ajanı + UI kendi bearer token'ıyla gelir
CREATE TABLE api_keys (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  label       TEXT NOT NULL,              -- serbest etiket: 'dev-a', 'dev-b', 'ci', 'ui'
  key_hash    TEXT NOT NULL UNIQUE,       -- sha256(token) — düz token asla saklanmaz
  role        TEXT NOT NULL DEFAULT 'agent' CHECK (role IN ('agent','ui','admin')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used   TIMESTAMPTZ
);
CREATE INDEX ON api_keys (key_hash);

-- ─────────────────────────────────────────────────────────────
-- Çalışma katmanı (hepsi project_id ile izole)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE agents (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  name            TEXT NOT NULL,          -- ajan etiketi (serbest), örn: dev-a / dev-b / ci
  machine         TEXT,
  status          TEXT NOT NULL DEFAULT 'offline'
                    CHECK (status IN ('offline','idle','working','blocked')),
  current_task_id UUID,
  note            TEXT,                   -- "şu an ne yapıyor" serbest metin
  last_heartbeat  TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (project_id, name)
);

CREATE TABLE tasks (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id   UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  title        TEXT NOT NULL,
  spec         TEXT,                      -- ayrıntılı görev tanımı
  status       TEXT NOT NULL DEFAULT 'todo'
                 CHECK (status IN ('todo','claimed','in_progress','blocked','review','done')),
  assign_mode  TEXT NOT NULL DEFAULT 'auto'  CHECK (assign_mode IN ('auto','manual')),
  assignee     TEXT,                      -- ajan adı (manuel atama veya claim eden)
  depends_on   UUID[] NOT NULL DEFAULT '{}',   -- tüm bağımlılıklar 'done' olmadan claim edilemez
  priority     INT NOT NULL DEFAULT 0,        -- büyük = önce
  lease_until  TIMESTAMPTZ,               -- kilit süresi; geçerse yeniden claim edilebilir (crash direnci)
  board_order  DOUBLE PRECISION NOT NULL DEFAULT 0,  -- kanban içi sıralama
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
  to_agent    TEXT,                       -- NULL = broadcast (herkese)
  task_id     UUID,
  body        TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  read_by     TEXT[] NOT NULL DEFAULT '{}'
);
CREATE INDEX ON messages (project_id, id);

-- Danışsal kaynak kilidi (aynı dosyaya iki ajan aynı anda dokunmasın)
CREATE TABLE locks (
  project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  resource    TEXT NOT NULL,              -- 'file:services/x/y.py' gibi
  held_by     TEXT NOT NULL,
  expires_at  TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (project_id, resource)
);

-- Append-only olay günlüğü (UI feed + denetim)
CREATE TABLE activity (
  id          BIGSERIAL PRIMARY KEY,
  project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  actor       TEXT,
  kind        TEXT NOT NULL,              -- task.created, task.claimed, task.done, message.posted, agent.status...
  detail      JSONB NOT NULL DEFAULT '{}',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON activity (project_id, id DESC);

-- Bellek defteri — ajanların kalıcı, zaman-damgalı notları/kararları/devirleri (handoff)
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

-- Async çalışma: her ajanın en son "senkron" olduğu an (sync catch-up için)
ALTER TABLE agents ADD COLUMN IF NOT EXISTS last_synced_at TIMESTAMPTZ;
