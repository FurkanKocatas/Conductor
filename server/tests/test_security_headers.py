"""Security headers must be present on every response, including errors."""
import dataclasses

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.mark.parametrize("header,expected", [
    ("x-content-type-options", "nosniff"),
    ("x-frame-options", "DENY"),
    ("referrer-policy", "strict-origin-when-cross-origin"),
])
def test_headers_present_on_success(header, expected):
    r = client.get("/health")
    assert r.headers[header] == expected


def test_csp_locks_down_dangerous_directives():
    csp = client.get("/health").headers["content-security-policy"]
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'none'" in csp
    assert "default-src 'self'" in csp


def test_csp_forbids_inline_scripts():
    """Regression guard: the SPA ships external bundles and theme-init.js is a
    separate file, so nothing should reintroduce 'unsafe-inline' for scripts."""
    csp = client.get("/health").headers["content-security-policy"]
    assert "script-src 'self';" in csp
    assert "script-src 'self' 'unsafe-inline'" not in csp


def test_headers_present_on_error_response():
    """A 401/501 must be just as protected as a 200."""
    r = client.get("/api/dash/me", headers={"Authorization": "Bearer x"})
    assert r.status_code >= 400
    assert r.headers["x-content-type-options"] == "nosniff"


def test_no_hsts_outside_production():
    """HSTS on a plain-HTTP dev origin would be a foot-gun."""
    assert "strict-transport-security" not in client.get("/health").headers


def test_hsts_enabled_in_production(monkeypatch):
    from app import security
    prod = dataclasses.replace(security.settings, environment="production")
    monkeypatch.setattr(security, "settings", prod)
    mw = security.SecurityHeadersMiddleware(app=lambda *a, **k: None)
    assert b"strict-transport-security" in mw.headers
    assert b"max-age=31536000" in mw.headers[b"strict-transport-security"]
