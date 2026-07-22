"""Live output stream — endpoint wiring, validation, and migration integrity.

The producer/consumer round-trip against a real DB is covered by the integration
run; these are the fast checks that don't need Postgres.
"""
import importlib.util
import pathlib


def test_stream_routes_registered():
    from app.main import app
    paths = set(app.openapi()["paths"])
    assert "/api/stream" in paths


def test_stream_kinds_constant():
    from app.main import _STREAM_KINDS
    assert set(_STREAM_KINDS) == {"text", "tool", "result", "sys"}


def test_emit_is_an_mcp_tool():
    """The agent-facing emit tool must exist so a Claude can stream its own work."""
    from app import main
    assert callable(main.emit)


def test_migration_0005_prunes_stream_in_reaper():
    """Regression: the reaper must prune stream_events, and the migration must not
    drop the existing task-recovery behaviour when it replaces the function."""
    path = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0005_stream.py"
    spec = importlib.util.spec_from_file_location("m0005", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.down_revision == "0004_billing"
    # We can't run SQL here, but we can assert the migration text keeps the
    # task-reclaim logic alongside the new prune, since it does CREATE OR REPLACE.
    import inspect
    src = inspect.getsource(mod.upgrade)
    assert "DELETE FROM stream_events" in src
    assert "task.reclaimed" in src          # existing behaviour preserved
    assert "prompt_state='pending'" in src  # existing behaviour preserved
