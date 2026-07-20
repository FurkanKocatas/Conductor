"""Stripe billing state on orgs + a processed-events ledger

Billing is the on-switch: an org is only usable while status='active', which is
set by a Stripe webhook. The `billing_events` table makes webhook handling
idempotent — Stripe retries deliveries and can send events out of order, so every
event id is recorded once and replays become no-ops.

org.status values: pending | active | suspended | deleted
  pending    — created (via Clerk) but never had an active subscription
  active     — paid and usable
  suspended  — subscription canceled / payment failed; data retained, access refused
  deleted    — org removed in Clerk (soft delete)

Revision ID: 0004_billing
Revises: 0003_clerk_org_binding
Create Date: 2026-07-19
"""
from alembic import op

revision = "0004_billing"
down_revision = "0003_clerk_org_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE orgs ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT")
    op.execute("ALTER TABLE orgs ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT")
    op.execute("ALTER TABLE orgs ADD COLUMN IF NOT EXISTS current_period_end TIMESTAMPTZ")
    op.execute("ALTER TABLE orgs ADD COLUMN IF NOT EXISTS provisioned_at TIMESTAMPTZ")
    # One Stripe customer maps to at most one org.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS orgs_stripe_customer_key
        ON orgs (stripe_customer_id) WHERE stripe_customer_id IS NOT NULL""")

    # Idempotency ledger: an event is processed at most once.
    op.execute("""
        CREATE TABLE IF NOT EXISTS billing_events (
          event_id    TEXT PRIMARY KEY,
          type        TEXT NOT NULL,
          org_id      UUID,
          received_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")
    op.execute("CREATE INDEX IF NOT EXISTS billing_events_recv_idx "
               "ON billing_events (received_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS billing_events")
    op.execute("DROP INDEX IF EXISTS orgs_stripe_customer_key")
    for col in ("stripe_customer_id", "stripe_subscription_id",
                "current_period_end", "provisioned_at"):
        op.execute(f"ALTER TABLE orgs DROP COLUMN IF EXISTS {col}")
