"""Dashboard helpers + the Clerk-disabled fallback (no DB required)."""
import pytest

from app.saas import _slugify


@pytest.mark.parametrize("name,expected", [
    ("My Project", "my-project"),
    ("  Spaces  ", "spaces"),
    ("Weird!!!Chars", "weird-chars"),
    ("a//b", "a-b"),
    ("---", "project"),
    ("", "project"),
    ("Ünïcode 99", "n-code-99"),
])
def test_slugify(name, expected):
    assert _slugify(name) == expected


def test_dashboard_routes_registered():
    """Assert via the OpenAPI schema: modern FastAPI keeps included routers as a
    single marker in app.routes rather than flattening them, so inspecting
    app.routes would be version-fragile."""
    from app.main import app
    paths = set(app.openapi()["paths"])
    assert "/api/dash/me" in paths
    assert "/api/dash/projects" in paths
    assert "/api/dash/projects/{project_id}/keys" in paths
    assert "/api/dash/keys/{key_id}" in paths
    assert "/api/webhooks/clerk" in paths


def test_dashboard_returns_501_when_clerk_disabled():
    """With Clerk unconfigured the agent API still works; the human API is off."""
    from fastapi.testclient import TestClient
    from app.main import app
    # No lifespan → no DB needed; the dependency short-circuits before any query.
    client = TestClient(app)
    r = client.get("/api/dash/me", headers={"Authorization": "Bearer whatever"})
    assert r.status_code == 501
