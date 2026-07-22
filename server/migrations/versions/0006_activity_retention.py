"""bound the activity feed's growth

activity is append-only and high-volume (every claim/heartbeat/message writes a
row), so it grows without limit. The reaper is extended to age it out after
180 days — long enough to stay a useful audit/feed, short enough to stop the
table growing forever. messages and memory are user content and are left intact;
a proper archival strategy for those is a follow-up.

Revision ID: 0006_activity_retention
Revises: 0005_stream
Create Date: 2026-07-22
"""
from alembic import op

revision = "0006_activity_retention"
down_revision = "0005_stream"
branch_labels = None
depends_on = None

_REAPER_WITH_ACTIVITY_PRUNE = """
CREATE OR REPLACE FUNCTION conductor_reap() RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  WITH reclaimed AS (
    UPDATE tasks SET status='todo', assignee=NULL, lease_until=NULL, updated_at=now()
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

  DELETE FROM stream_events WHERE created_at < now() - interval '1 day';
  DELETE FROM activity      WHERE created_at < now() - interval '180 days';
END $$;
"""

_REAPER_0005 = """
CREATE OR REPLACE FUNCTION conductor_reap() RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  WITH reclaimed AS (
    UPDATE tasks SET status='todo', assignee=NULL, lease_until=NULL, updated_at=now()
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

  DELETE FROM stream_events WHERE created_at < now() - interval '1 day';
END $$;
"""


def upgrade() -> None:
    op.execute(_REAPER_WITH_ACTIVITY_PRUNE)


def downgrade() -> None:
    op.execute(_REAPER_0005)
