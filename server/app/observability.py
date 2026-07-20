"""Error tracking — optional, enabled only when SENTRY_DSN is set.

Kept as a lazy import so the dependency is never required to run or test the app,
and so a self-hosted install has nothing phoning home by default.
"""
from __future__ import annotations

from .config import settings
from .logging_setup import get_logger

log = get_logger("conductor.observability")


def init_error_tracking() -> bool:
    """Initialize Sentry if configured. Returns True when active."""
    if not settings.sentry_dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        log.warning("SENTRY_DSN is set but sentry-sdk is not installed")
        return False

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        # Conservative default: errors always, a small slice of traces.
        traces_sample_rate=settings.sentry_traces_sample_rate,
        # Request bodies and headers can carry bearer tokens — never send them.
        send_default_pii=False,
        max_request_body_size="never",
    )
    log.info("error tracking enabled", extra={"ctx": {"environment": settings.environment}})
    return True
