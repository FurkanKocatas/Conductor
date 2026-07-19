"""baseline schema — orgs/projects/api_keys + agents/tasks/messages/locks/activity/memory

Consolidates the former db/001_init.sql plus every additive change that used to
live in the in-code migrate(): memory table, agents.last_synced_at/branch/git,
projects.brief, tasks.prompt/prompt_state/prompt_history/prompt_lease.

This is the single source of truth for the schema from here on. db/001_init.sql
is retained only as a reference snapshot and is no longer applied at boot.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-07-19
"""
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ── Tenant layer ─────────────────────────────────────────────
    op.execute("""
        CREATE TABLE orgs (
          id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          name        TEXT NOT NULL,
          slug        TEXT UNIQUE NOT NULL,
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")
    op.execute("""
        CREATE TABLE projects (
          id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          org_id      UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
          name        TEXT NOT NULL,
          slug        TEXT NOT NULL,
          brief       TEXT,
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (org_id, slug)
        )""")
    op.execute("""
        CREATE TABLE api_keys (
          id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          label       TEXT NOT NULL,
          key_hash    TEXT NOT NULL UNIQUE,
          role        TEXT NOT NULL DEFAULT 'agent' CHECK (role IN ('agent','ui','admin')),
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
          last_used   TIMESTAMPTZ
        )""")
    op.execute("CREATE INDEX api_keys_hash_idx ON api_keys (key_hash)")

    # ── Work layer (all project_id-scoped) ───────────────────────
    op.execute("""
        CREATE TABLE agents (
          id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          name            TEXT NOT NULL,
          machine         TEXT,
          status          TEXT NOT NULL DEFAULT 'offline'
                            CHECK (status IN ('offline','idle','working','blocked')),
          current_task_id UUID,
          note            TEXT,
          branch          TEXT,
          git             JSONB NOT NULL DEFAULT '{}',
          last_heartbeat  TIMESTAMPTZ,
          last_synced_at  TIMESTAMPTZ,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (project_id, name)
        )""")
    op.execute("""
        CREATE TABLE tasks (
          id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          project_id   UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          title        TEXT NOT NULL,
          spec         TEXT,
          status       TEXT NOT NULL DEFAULT 'todo'
                         CHECK (status IN ('todo','claimed','in_progress','blocked','review','done')),
          assign_mode  TEXT NOT NULL DEFAULT 'auto' CHECK (assign_mode IN ('auto','manual')),
          assignee     TEXT,
          depends_on   UUID[] NOT NULL DEFAULT '{}',
          priority     INT NOT NULL DEFAULT 0,
          lease_until  TIMESTAMPTZ,
          board_order  DOUBLE PRECISION NOT NULL DEFAULT 0,
          artifacts    JSONB NOT NULL DEFAULT '{}',
          prompt         TEXT,
          prompt_state   TEXT NOT NULL DEFAULT 'idle',
          prompt_history JSONB NOT NULL DEFAULT '[]',
          prompt_lease   TIMESTAMPTZ,
          created_by   TEXT,
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")
    op.execute("CREATE INDEX tasks_board_idx ON tasks (project_id, status, priority DESC, created_at)")

    op.execute("""
        CREATE TABLE messages (
          id          BIGSERIAL PRIMARY KEY,
          project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          from_agent  TEXT,
          to_agent    TEXT,
          task_id     UUID,
          body        TEXT NOT NULL,
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
          read_by     TEXT[] NOT NULL DEFAULT '{}'
        )""")
    op.execute("CREATE INDEX messages_proj_idx ON messages (project_id, id)")

    op.execute("""
        CREATE TABLE locks (
          project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          resource    TEXT NOT NULL,
          held_by     TEXT NOT NULL,
          expires_at  TIMESTAMPTZ NOT NULL,
          PRIMARY KEY (project_id, resource)
        )""")

    op.execute("""
        CREATE TABLE activity (
          id          BIGSERIAL PRIMARY KEY,
          project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          actor       TEXT,
          kind        TEXT NOT NULL,
          detail      JSONB NOT NULL DEFAULT '{}',
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")
    op.execute("CREATE INDEX activity_proj_idx ON activity (project_id, id DESC)")

    op.execute("""
        CREATE TABLE memory (
          id          BIGSERIAL PRIMARY KEY,
          project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          author      TEXT,
          kind        TEXT NOT NULL DEFAULT 'note',
          body        TEXT NOT NULL,
          task_id     UUID,
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")
    op.execute("CREATE INDEX memory_proj_idx ON memory (project_id, id DESC)")


def downgrade() -> None:
    for tbl in ("memory", "activity", "locks", "messages", "tasks", "agents",
                "api_keys", "projects", "orgs"):
        op.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")
