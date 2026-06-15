"""
logging_config.py — Structured logging setup for DENZO-SEO.

Replaces print() calls with structured JSON-line logging.
Each log line includes timestamp, level, agent, tenant, and message.

Usage:
    from denzo.logging_config import get_logger
    logger = get_logger(__name__)
    logger.info("Page generated", extra={"tenant_id": tenant_id, "page_count": 5})
"""

import logging
import sys
import json
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """One JSON object per line for machine parsing."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
        }
        # Extra fields passed via extra={} in logger call
        for key in ("tenant_id", "agent", "page_id", "keyword", "duration_ms", "status"):
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val

        payload["msg"] = record.getMessage()

        if record.exc_info and record.exc_info[1]:
            payload["exc"] = str(record.exc_info[1])

        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO"):
    """Configure root logger with JSON output to stdout.

    Called once at app startup (in create_app).
    """
    root = logging.getLogger()
    # Remove any existing handlers
    for h in root.handlers[:]:
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Silence noisy libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("apify_client").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    return root


def get_logger(name: str) -> logging.Logger:
    """Get a logger for a module. Prefer this over print()."""
    return logging.getLogger(name)
