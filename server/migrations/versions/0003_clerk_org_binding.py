"""bind orgs to Clerk organizations + billing state columns

Phase 1 (identity): `clerk_org_id` is the join between a Clerk Organization and a
Conductor org. It is the tenant boundary — a Clerk session JWT carries the user's
active org, which resolves to exactly one row here.

Phase 2 (billing) will use `plan` and `status`: a workspace is only usable while
`status='active'`. Added now (nullable / defaulted) so the billing phase is a
pure code change, not another migration against live data.

Revision ID: 0003_clerk_org_binding
Revises: 0002_registry_presence_reaper
Create Date: 2026-07-19
"""
from alembic import op

revision = "0003_clerk_org_binding"
down_revision = "0002_registry_presence_reaper"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE orgs ADD COLUMN IF NOT EXISTS clerk_org_id TEXT")
    # Partial unique index: dev-seeded orgs have NULL clerk_org_id and must not
    # collide with each other.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS orgs_clerk_org_id_key
        ON orgs (clerk_org_id) WHERE clerk_org_id IS NOT NULL""")

    # Billing state (Phase 2). 'active' by default so self-hosted / dev installs
    # keep working without any subscription.
    op.execute("ALTER TABLE orgs ADD COLUMN IF NOT EXISTS plan TEXT")
    op.execute("""
        ALTER TABLE orgs ADD COLUMN IF NOT EXISTS status TEXT NOT NULL
        DEFAULT 'active'""")

    # Who created a project (Clerk user id), for attribution in the dashboard.
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS created_by TEXT")

    # Key management: soft-revoke instead of delete, so an audit trail survives.
    op.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ")
    op.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS created_by TEXT")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS orgs_clerk_org_id_key")
    op.execute("ALTER TABLE orgs DROP COLUMN IF EXISTS clerk_org_id")
    op.execute("ALTER TABLE orgs DROP COLUMN IF EXISTS plan")
    op.execute("ALTER TABLE orgs DROP COLUMN IF EXISTS status")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS created_by")
    op.execute("ALTER TABLE api_keys DROP COLUMN IF EXISTS revoked_at")
    op.execute("ALTER TABLE api_keys DROP COLUMN IF EXISTS created_by")
