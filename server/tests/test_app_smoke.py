"""App imports cleanly and the expected routes are wired — no DB needed."""


def test_routes_registered():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/health" in paths
    assert "/ready" in paths
    assert "/api/state" in paths
    assert "/api/inbox" in paths


def test_health_is_dependency_free():
    # /health must not require the DB (liveness); calling the handler directly works.
    import asyncio
    from app.main import health
    assert asyncio.run(health())["ok"] is True
