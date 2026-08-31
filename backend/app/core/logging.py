"""Logging setup.

Structured, key=value logging on stdlib ``logging``. Never log passwords, tokens,
API keys, or full chat message contents (chat messages may contain private user data).
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

_LOG_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s %(fields)s"


class _FieldsFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "fields"):
            record.fields = ""
        return super().format(record)


def configure_logging(level: str) -> None:
    """Install the application-wide handler/formatter on the root logger."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_FieldsFormatter(_LOG_FORMAT, datefmt="%Y-%m-%dT%H:%M:%S%z"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())


def get_logger(name: str) -> logging.Logger:
    """Named logger for a module."""
    return logging.getLogger(name)


def structured(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    """Log a message with structured key=value fields appended."""
    rendered = " ".join(f"{key}={value}" for key, value in fields.items())
    logger.log(level, message, extra={"fields": rendered})


def log_extra_fields(record: logging.LogRecord) -> MutableMapping[str, Any]:
    """Accessor for tests inspecting structured fields."""
    return record.__dict__
