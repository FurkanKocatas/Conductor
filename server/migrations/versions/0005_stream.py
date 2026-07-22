"""live agent output stream (watch a teammate's Claude in real time)

An agent's listener (or the agent's Claude via the `emit` MCP tool) appends chunks
of what it's doing — thoughts, tool calls, results — as it works. Anyone in the
project can then tail that stream, which is how the board's live task terminal
works. Conductor stays generic: it stores and serves the stream, it does not run
Claude.

Stream rows are high-volume and disposable, so conductor_reap() is extended to
prune them (older than a day) on the same schedule as the task reaper.

Revision ID: 0005_stream
Revises: 0004_billing
Create Date: 2026-07-22
"""
from alembic import op

revision = "0005_stream"
down_revision = "0004_billing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS stream_events (
          id          BIGSERIAL PRIMARY KEY,
          project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          agent       TEXT NOT NULL,                 -- the producing agent's label
          task_id     UUID,
          kind        TEXT NOT NULL DEFAULT 'text'
                        CHECK (kind IN ('text','tool','result','sys')),
          content     TEXT NOT NULL,
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")
    # Tail query is (project, agent, id > since) — index matches it exactly.
    op.execute("CREATE INDEX IF NOT EXISTS stream_events_tail_idx "
               "ON stream_events (project_id, agent, id)")
    op.execute("CREATE INDEX IF NOT EXISTS stream_events_age_idx "
               "ON stream_events (created_at)")

    # Extend the existing reaper to also prune old stream rows. Same body as
    # migration 0002 plus one DELETE, so it runs under pg_cron in production and
    # under the in-process fallback locally, with no extra schedule.
    op.execute("""
        CREATE OR REPLACE FUNCTION conductor_reap() RETURNS void
        LANGUAGE plpgsql AS $$
        BEGIN
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

          UPDATE agents SET status='offline', current_task_id=NULL
          WHERE status <> 'offline' AND last_heartbeat IS NOT NULL
            AND last_heartbeat < now() - interval '2 minutes';

          UPDATE tasks SET prompt_state='pending', prompt_lease=NULL, updated_at=now()
          WHERE prompt_state='running' AND prompt_lease IS NOT NULL
            AND prompt_lease < now();

          -- prune disposable stream output
          DELETE FROM stream_events WHERE created_at < now() - interval '1 day';
        END $$;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS stream_events")
    # Restore the 0002 reaper body (without the stream prune).
    op.execute("""
        CREATE OR REPLACE FUNCTION conductor_reap() RETURNS void
        LANGUAGE plpgsql AS $$
        BEGIN
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

          UPDATE agents SET status='offline', current_task_id=NULL
          WHERE status <> 'offline' AND last_heartbeat IS NOT NULL
            AND last_heartbeat < now() - interval '2 minutes';

          UPDATE tasks SET prompt_state='pending', prompt_lease=NULL, updated_at=now()
          WHERE prompt_state='running' AND prompt_lease IS NOT NULL
            AND prompt_lease < now();
        END $$;
    """)
