"""Structured stderr logging for pm_manage_csvs.

MCP stdio traffic uses stdout, so all logs go to stderr. Format is
one JSON line per event for easy grep/parsing in hermes gateway.log.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from typing import Any

import structlog

_CONFIGURED = False


def configure_logging(level: str = "INFO") -> None:
    """Idempotently configure structlog for stderr output."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            _add_iso_timestamp,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(structlog.stdlib, level.upper(), 20)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True


def _add_iso_timestamp(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    event_dict["ts"] = datetime.now(UTC).isoformat()
    return event_dict


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a configured logger for `name`. Configures on first call."""
    configure_logging()
    return structlog.get_logger(name)


def log_event(event: str, **fields: Any) -> None:
    """Quick one-off event log without holding a logger reference."""
    logger = get_logger("pm_manage_csvs")
    logger.info(event, **fields)
