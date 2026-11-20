"""Structured JSON logging (structlog) to stderr."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


class _LazyStderr:
    """Resolves sys.stderr at write time, so a stream swapped later (pytest) is never kept."""

    def write(self, text: str) -> int:
        return sys.stderr.write(text)

    def flush(self) -> None:
        sys.stderr.flush()


def configure_logging(level: int = logging.INFO) -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(sort_keys=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=_LazyStderr()),  # type: ignore[arg-type]
        cache_logger_on_first_use=True,
    )


def get_logger(**initial: Any) -> Any:
    return structlog.get_logger().bind(**initial)
