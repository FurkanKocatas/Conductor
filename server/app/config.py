"""
Central configuration — all runtime settings resolved from the environment.

In production these come from Google Secret Manager (injected as env vars by
Cloud Run); locally from `.env` via docker-compose. Nothing is hardcoded and no
secret has a usable default. Import `settings` everywhere instead of reading
os.environ directly, so config has one source of truth and is testable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _csv(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    # ── Data plane ────────────────────────────────────────────────
    # DATABASE_URL is the CONTROL-plane / shared-tenant Postgres. Per-org
    # databases (the growth switch) are resolved at runtime from the
    # `tenant_databases` registry, not from env — see app/db.py.
    database_url: str

    # ── Identity / billing (wired in Phase 1 / 2; optional in Phase 0) ──
    clerk_jwks_url: str = ""
    clerk_issuer: str = ""
    clerk_webhook_secret: str = ""       # Svix signing secret for Clerk webhooks
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_id: str = ""            # the single paid plan's Price
    billing_plan_name: str = "pro"       # label stored on orgs.plan

    # Public origin used to build Stripe return URLs (checkout success/cancel,
    # billing portal return). e.g. https://app.example.com
    public_base_url: str = "http://localhost:8790"

    @property
    def billing_enabled(self) -> bool:
        """Billing is the on-switch for a workspace. When Stripe is NOT
        configured (self-hosted / dev), orgs are usable immediately; when it IS,
        a new org stays 'pending' until a subscription goes active."""
        return bool(self.stripe_secret_key and self.stripe_webhook_secret
                    and self.stripe_price_id)

    @property
    def clerk_enabled(self) -> bool:
        """Dashboard (human) auth is active only when Clerk is configured. When
        off, the Clerk-authed routes return 501 so the agent/REST surface still
        works standalone."""
        return bool(self.clerk_jwks_url and self.clerk_issuer)

    # ── HTTP / security ───────────────────────────────────────────
    # Explicit allow-list. "*" is only tolerated in dev and must never be set
    # in production — see validate().
    allowed_origins: list[str] = field(default_factory=list)

    # ── Pool sizing (kept small: Neon's pooled endpoint caps connections,
    #    and Cloud Run runs many small instances) ──
    db_pool_min: int = 1
    db_pool_max: int = 5

    # ── Reaper crash-recovery ─────────────────────────────────────
    #   pgcron  → scheduled inside Postgres (production; app stays stateless
    #             and can scale to zero).
    #   inproc  → legacy in-process loop (local dev without pg_cron only).
    #   off     → disabled (tests).
    reaper_mode: str = "pgcron"
    reaper_interval_s: int = 30

    # ── Dev conveniences ──────────────────────────────────────────
    # Seeds the Demo/default org + bootstrap admin for local self-hosting.
    # MUST be false in the SaaS: real workspaces are provisioned on purchase.
    dev_seed: bool = False
    bootstrap_admin_token: str = ""
    default_org: str = "Demo"
    default_project: str = "default"

    app_name: str = "Conductor"
    log_level: str = "INFO"
    log_json: bool = True
    environment: str = "development"   # development | staging | production

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    def validate(self) -> None:
        """Fail fast on misconfiguration rather than at first request."""
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is required")
        if self.is_production:
            if "*" in self.allowed_origins:
                raise RuntimeError("ALLOWED_ORIGINS must not be '*' in production")
            if not self.allowed_origins:
                raise RuntimeError("ALLOWED_ORIGINS is required in production")
            if self.dev_seed:
                raise RuntimeError("DEV_SEED must be off in production")
            if self.reaper_mode == "inproc":
                raise RuntimeError("REAPER_MODE=inproc is unsafe with >1 instance; use pgcron")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings(
        database_url=os.environ.get("DATABASE_URL", ""),
        clerk_jwks_url=os.environ.get("CLERK_JWKS_URL", ""),
        clerk_issuer=os.environ.get("CLERK_ISSUER", ""),
        clerk_webhook_secret=os.environ.get("CLERK_WEBHOOK_SECRET", ""),
        stripe_secret_key=os.environ.get("STRIPE_SECRET_KEY", ""),
        stripe_webhook_secret=os.environ.get("STRIPE_WEBHOOK_SECRET", ""),
        stripe_price_id=os.environ.get("STRIPE_PRICE_ID", ""),
        billing_plan_name=os.environ.get("BILLING_PLAN_NAME", "pro"),
        public_base_url=os.environ.get("PUBLIC_BASE_URL", "http://localhost:8790").rstrip("/"),
        allowed_origins=_csv("ALLOWED_ORIGINS", "*"),
        db_pool_min=int(os.environ.get("DB_POOL_MIN", "1")),
        db_pool_max=int(os.environ.get("DB_POOL_MAX", "5")),
        reaper_mode=os.environ.get("REAPER_MODE", "pgcron").strip().lower(),
        reaper_interval_s=int(os.environ.get("REAPER_INTERVAL_S", "30")),
        dev_seed=_bool("DEV_SEED", False),
        bootstrap_admin_token=os.environ.get("BOOTSTRAP_ADMIN_TOKEN", ""),
        default_org=os.environ.get("DEFAULT_ORG", "Demo"),
        default_project=os.environ.get("DEFAULT_PROJECT", "default"),
        app_name=os.environ.get("APP_NAME", "Conductor"),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        log_json=_bool("LOG_JSON", True),
        environment=os.environ.get("ENVIRONMENT", "development"),
    )
    return s


# Convenience singleton for import-site use.
settings = get_settings()
