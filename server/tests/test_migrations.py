"""Migration chain integrity — revisions link correctly and are runnable."""
import importlib.util
import pathlib

VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"


def _load(name):
    path = VERSIONS / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_revision_chain():
    baseline = _load("0001_baseline.py")
    second = _load("0002_registry_presence_reaper.py")
    assert baseline.down_revision is None
    assert baseline.revision == "0001_baseline"
    assert second.down_revision == baseline.revision
    for mod in (baseline, second):
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)
