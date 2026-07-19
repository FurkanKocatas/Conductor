"""Config validation — the production guardrails must actually fire."""
import pytest

from app.config import Settings, _bool, _csv


def _prod(**kw):
    base = dict(database_url="postgresql://x/y", allowed_origins=["https://app.example"],
                environment="production")
    base.update(kw)
    return Settings(**base)


def test_prod_rejects_wildcard_cors():
    with pytest.raises(RuntimeError):
        _prod(allowed_origins=["*"]).validate()


def test_prod_requires_origins():
    with pytest.raises(RuntimeError):
        _prod(allowed_origins=[]).validate()


def test_prod_rejects_inproc_reaper():
    with pytest.raises(RuntimeError):
        _prod(reaper_mode="inproc").validate()


def test_prod_rejects_dev_seed():
    with pytest.raises(RuntimeError):
        _prod(dev_seed=True).validate()


def test_missing_database_url():
    with pytest.raises(RuntimeError):
        Settings(database_url="").validate()


def test_valid_prod_config_passes():
    _prod().validate()   # should not raise


def test_dev_allows_wildcard_and_seed():
    Settings(database_url="postgresql://x/y", allowed_origins=["*"],
             environment="development", dev_seed=True, reaper_mode="inproc").validate()


def test_env_parsers(monkeypatch):
    monkeypatch.setenv("FLAG", "YnO".replace("YnO", "yes"))
    assert _bool("FLAG") is True
    monkeypatch.setenv("FLAG", "off")
    assert _bool("FLAG") is False
    assert _bool("MISSING", default=True) is True
    monkeypatch.setenv("LIST", "a, b ,, c")
    assert _csv("LIST") == ["a", "b", "c"]
