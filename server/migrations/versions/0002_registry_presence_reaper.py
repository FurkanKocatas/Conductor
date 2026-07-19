"""tenant registry + DB-backed presence + in-database reaper (pg_cron)

Three SaaS foundations:
  1. tenant_databases  — control-plane registry mapping an org to a dedicated DB
     (the growth switch). Empty by default → every org stays on the shared DB.
  2. presence          — replaces the in-process _PAGE_PRESENCE dict so "who's
     viewing the board" is correct across replicas and cold starts.
  3. conductor_reap()  — the crash-recovery loop, moved OUT of the app process
     into a SQL function scheduled by pg_cron. Runs while the app is scaled to
     zero and removes the multi-replica race entirely.

Revision ID: 0002_registry_presence_reaper
Revises: 0001_baseline
Create Date: 2026-07-19
"""
from alembic import op

revision = "0002_registry_presence_reaper"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Tenant registry (control plane). `dsn` should reference a secret in
    #    production (Secret Manager), not a literal — tracked for Phase 3.
    op.execute("""
        CREATE TABLE IF NOT EXISTS tenant_databases (
          org_id      UUID PRIMARY KEY,
          dsn         TEXT NOT NULL,
          active      BOOLEAN NOT NULL DEFAULT true,
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")

    # 2) Presence (data plane).
    op.execute("""
        CREATE TABLE IF NOT EXISTS presence (
          project_id  UUID NOT NULL,
          label       TEXT NOT NULL,
          role        TEXT,
          last_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (project_id, label)
        )""")

    # 3) Reaper as a SQL function (data plane). Mirrors the former _reaper():
    #    reclaim expired leases, offline stale agents, re-pending crashed prompts.
    op.execute("""
        CREATE OR REPLACE FUNCTION conductor_reap() RETURNS void
        LANGUAGE plpgsql AS $$
        BEGIN
          -- (a) reclaim tasks whose lease expired, logging each reclaim
          WITH reclaimed AS (
            UPDATE tasks SET status='todo', assignee=NULL, lease_until=NULL,
                             updated_at=now()
            WHERE status IN ('claimed','in_progress')
              AND lease_until IS NOT NULL AND lease_until < now()
            RETURNING id, project_id, title
          )
          INSERT INTO activity (project_id, actor, kind, detail)
          SELECT project_id, 'reaper', 'task.reclaimed',
                 jsonb_build_object('task_id', id::text, 'title', title)
          FROM reclaimed;

          -- (b) mark agents offline after 2 minutes of silence
          UPDATE agents SET status='offline', current_task_id=NULL
          WHERE status <> 'offline' AND last_heartbeat IS NOT NULL
            AND last_heartbeat < now() - interval '2 minutes';

          -- (c) re-queue crashed automation prompts (card stays put)
          UPDATE tasks SET prompt_state='pending', prompt_lease=NULL, updated_at=now()
          WHERE prompt_state='running' AND prompt_lease IS NOT NULL
            AND prompt_lease < now();
        END $$;
    """)

    # Schedule it with pg_cron if the extension is available (Neon: enable it;
    # some local images lack it — then run REAPER_MODE=inproc for dev only).
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name='pg_cron') THEN
            CREATE EXTENSION IF NOT EXISTS pg_cron;
            PERFORM cron.unschedule('conductor-reap')
              WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname='conductor-reap');
            PERFORM cron.schedule('conductor-reap', '* * * * *', 'SELECT conductor_reap()');
          END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_extension WHERE extname='pg_cron')
             AND EXISTS (SELECT 1 FROM cron.job WHERE jobname='conductor-reap') THEN
            PERFORM cron.unschedule('conductor-reap');
          END IF;
        END $$;
    """)
    op.execute("DROP FUNCTION IF EXISTS conductor_reap()")
    op.execute("DROP TABLE IF EXISTS presence")
    op.execute("DROP TABLE IF EXISTS tenant_databases")
