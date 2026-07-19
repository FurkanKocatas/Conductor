"""
Structured logging — JSON lines in production (parsed by Cloud Logging), plain
text locally. Import `logger` or call `get_logger(__name__)`.

Kept dependency-free (stdlib only) on purpose: no structlog to install, and the
JSON shape maps onto Google Cloud Logging's expected fields (severity, message).
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from .config import settings

# Cloud Logging reads "severity"; Python uses "levelname". Map between them.
_SEVERITY = {
    "DEBUG": "DEBUG", "INFO": "INFO", "WARNING": "WARNING",
    "ERROR": "ERROR", "CRITICAL": "CRITICAL",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.now(timezone.utc).isoformat(),
            "severity": _SEVERITY.get(record.levelname, record.levelname),
            "message": record.getMessage(),
            "logger": record.name,
        }
        # Attach any structured extras passed via logger.info(..., extra={"ctx": {...}})
        ctx = getattr(record, "ctx", None)
        if isinstance(ctx, dict):
            payload.update(ctx)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging() -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    if settings.log_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s — %(message)s"))
    root.addHandler(handler)
    root.setLevel(settings.log_level)
    # uvicorn access logs are noisy + duplicate; let our handler carry them.
    for noisy in ("uvicorn.access",):
        logging.getLogger(noisy).handlers.clear()
        logging.getLogger(noisy).propagate = True


def get_logger(name: str = "conductor") -> logging.Logger:
    return logging.getLogger(name)


logger = get_logger()
