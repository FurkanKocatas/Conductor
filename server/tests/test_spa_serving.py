"""The built SPA is served with a client-side-routing fallback."""
import os

import pytest
from fastapi.testclient import TestClient

from app.main import app

STATIC = os.path.join(os.path.dirname(__file__), "..", "static")
built = pytest.mark.skipif(
    not os.path.isfile(os.path.join(STATIC, "index.html")),
    reason="frontend not built (run `npm run build` in web/)")

client = TestClient(app)


@built
def test_root_serves_the_app():
    r = client.get("/")
    assert r.status_code == 200
    assert "<div id=\"root\">" in r.text


@built
def test_deep_link_falls_back_to_index():
    """/board has no file on disk — the router owns it, so index.html must be
    returned rather than a 404."""
    r = client.get("/board")
    assert r.status_code == 200
    assert "<div id=\"root\">" in r.text


@built
def test_api_routes_are_not_shadowed_by_the_spa_mount():
    """The catch-all mount must never swallow the API."""
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == "conductor"


@built
def test_theme_init_is_served_as_a_real_file():
    r = client.get("/theme-init.js")
    assert r.status_code == 200
    assert "conductor_theme" in r.text
