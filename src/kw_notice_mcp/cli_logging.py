"""Structured, stderr-only operational logging for the local CLI."""

import json
import logging
import sys
from typing import Final

from kw_notice_mcp.collector_models import CollectionResult
from kw_notice_mcp.storage_models import CrawlRun

_LOGGER_NAME: Final = "kw_notice_mcp"
_LEVELS: Final = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


def configure_logging(level: str) -> logging.Logger:
    """Install one JSON handler on stderr and return the application logger."""
    logger = logging.getLogger(_LOGGER_NAME)
    logger.handlers.clear()
    logger.propagate = False
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(_LEVELS[level])
    return logger


def log_collection(
    logger: logging.Logger,
    result: CollectionResult,
    crawl: CrawlRun | None,
) -> None:
    """Emit one safe run record with counters but no response content."""
    payload: dict[str, str | int | None] = {
        "event": "crawl.finished",
        "run_id": result.run_id,
        "status": result.status.value,
        "page_count": crawl.pages_seen if crawl is not None else 0,
        "detail_count": crawl.detail_requests if crawl is not None else 0,
        "wire_requests": result.wire_requests,
        "retry_count": result.retry_count,
        "block_reason": result.reason,
    }
    logger.info(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def log_cli_failure(logger: logging.Logger, status: str, reason: str) -> None:
    """Emit a bounded CLI failure record without echoing untrusted values."""
    payload: dict[str, str | int | None] = {
        "event": "cli.failure",
        "run_id": None,
        "status": status,
        "page_count": 0,
        "detail_count": 0,
        "block_reason": reason,
    }
    logger.error(json.dumps(payload, ensure_ascii=False, sort_keys=True))
