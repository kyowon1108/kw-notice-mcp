"""Validation, redaction, and error helpers for the MCP read boundary."""

import re
from datetime import date
from typing import Final

from kw_notice_mcp.domain import CategorySpec, category_from_id, category_from_name
from kw_notice_mcp.redaction import redact_human_fields
from kw_notice_mcp.responses import ErrorEnvelope, NoticeSummary
from kw_notice_mcp.storage import SQLiteNoticeStore
from kw_notice_mcp.storage_models import StoredNotice

DUID_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9]{1,12}")
DATE_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
MAX_QUERY_LENGTH: Final = 200
MAX_LIMIT: Final = 50
MAX_OFFSET: Final = 500


def invalid(parameter: str, message: str) -> ErrorEnvelope:
    """Build a bounded invalid-input envelope."""
    return ErrorEnvelope(
        code="invalid_input", message=f"{parameter}: {message}", retryable=False
    )


def storage_error(error: Exception) -> ErrorEnvelope:
    """Translate any expected storage failure without exposing internals."""
    del error
    return ErrorEnvelope(
        code="storage_unavailable",
        message="Notice storage is unavailable.",
        retryable=True,
    )


def blocked() -> ErrorEnvelope:
    """Build the retryable cache-preserving blocked result."""
    return ErrorEnvelope(
        code="blocked",
        message="The latest crawl is blocked; returning cached results.",
        retryable=True,
    )


def category(value: str | None) -> tuple[CategorySpec | None, ErrorEnvelope | None]:
    """Parse one exact canonical category ID or Korean name."""
    if value is None:
        return None, None
    if type(value) is not str or value != value.strip():
        return None, invalid("category", "must be an exact category ID or name")
    result = category_from_id(value) or category_from_name(value)
    if result is None:
        return None, invalid("category", "unknown category")
    return result, None


def published_date(
    value: str | None, parameter: str
) -> tuple[date | None, ErrorEnvelope | None]:
    """Parse a strict ISO calendar date for an inclusive filter."""
    if value is None:
        return None, None
    if type(value) is not str or DATE_PATTERN.fullmatch(value) is None:
        return None, invalid(parameter, "must be an ISO date YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None, invalid(parameter, "must be an ISO date YYYY-MM-DD")
    return parsed, None


def page(limit: int, offset: int) -> ErrorEnvelope | None:
    """Validate the fixed result-page bounds."""
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        return invalid("limit", "must be between 1 and 50")
    if type(offset) is not int or not 0 <= offset <= MAX_OFFSET:
        return invalid("offset", "must be between 0 and 500")
    return None


def summary(store: SQLiteNoticeStore, notice: StoredNotice) -> NoticeSummary:
    """Convert one stored notice to its bounded response projection."""
    fields = redact_human_fields(
        title=notice.title,
        category_name=notice.category_name,
        department=notice.department,
        body=notice.body or "",
    )
    return NoticeSummary(
        duid=str(notice.duid),
        title=fields.title,
        category_id=str(notice.category_id),
        category=fields.category_name,
        posted_date=notice.posted_date.isoformat(),
        updated_date=notice.updated_date.isoformat(),
        department=fields.department,
        source_url=str(notice.source_url),
        attachments_present=notice.attachments_present,
        collected_at=notice.collected_at.isoformat(),
        freshness=store.freshness(notice.duid).value,
    )


def bounded_body(notice: StoredNotice) -> str | None:
    """Redact and cap a stored body again at the MCP output boundary."""
    if notice.body is None:
        return None
    return redact_human_fields(
        title=notice.title,
        category_name=notice.category_name,
        department=notice.department,
        body=notice.body,
    ).body


def pagination(
    item_count: int, limit: int, offset: int, total: int
) -> tuple[int | None, bool]:
    """Calculate the fixed next-offset contract from an exact count."""
    has_more = offset + item_count < total
    return (offset + limit if has_more else None), has_more
