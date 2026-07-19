"""Test env defaults. Set before app modules import config (a singleton)."""
import os

os.environ.setdefault("ALLOWED_ORIGINS", "*")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("REAPER_MODE", "off")
os.environ.setdefault("DEV_SEED", "false")
os.environ.setdefault("LOG_JSON", "false")
# DATABASE_URL is intentionally left unset unless the CI/dev DB provides it;
# integration tests skip when it's absent.
