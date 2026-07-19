"""End-to-end against a real Postgres. Skipped unless DATABASE_URL is set and
migrations have been applied (CI provides both). Exercises the app lifespan:
pool init, health/ready, and auth rejection."""
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="no DATABASE_URL (unit-only run)")


def test_lifespan_health_ready_and_auth():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:              # runs lifespan → connects DB
        assert client.get("/health").status_code == 200
        assert client.get("/ready").json()["db"] == "up"
        # No bearer token → 401 on a protected route.
        assert client.get("/api/state").status_code == 401
