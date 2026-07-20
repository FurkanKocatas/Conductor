"""Small shared helpers used by both the agent API (main) and the dashboard (saas)."""
from __future__ import annotations

import hashlib
import json


def sha256_hex(value: str) -> str:
    """Hash an API token. Plain tokens are never stored — only this digest."""
    return hashlib.sha256(value.encode()).hexdigest()


def ser(row) -> dict:
    """asyncpg Record → JSON-compatible dict (UUID/datetime → str)."""
    return json.loads(json.dumps(dict(row), default=str))
