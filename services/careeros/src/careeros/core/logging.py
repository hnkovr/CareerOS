"""structlog configuration: JSON in prod, pretty console in dev; request-id bound per request."""

from __future__ import annotations

import logging
import sys

import structlog
from structlog.types import EventDict

from careeros.core.config import Settings

_SENSITIVE_KEYS = {"api_key", "token", "authorization", "password", "secret", "body", "raw_text"}


def _redact(_logger: object, _method: str, event: EventDict) -> EventDict:
    for key in list(event):
        if key.lower() in _SENSITIVE_KEYS or key.lower().endswith(("_key", "_token", "_secret")):
            event[key] = "***"
    return event


def configure_logging(settings: Settings) -> None:
    level = logging.getLevelNamesMapping().get(settings.log_level.upper(), logging.INFO)
    logging.basicConfig(level=level, stream=sys.stdout, format="%(message)s")

    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if settings.log_json or settings.env == "prod"
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name) if name else structlog.get_logger()
