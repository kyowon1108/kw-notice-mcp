"""Pure conversions and bounded values shared by SQLite storage modules."""

import hashlib
from datetime import UTC, date, datetime
from typing import Final, Protocol

from kw_notice_mcp.domain import CategoryId, CategoryName, NoticeDetail, NoticeSummary
from kw_notice_mcp.redaction import redact_human_fields
from kw_notice_mcp.storage_errors import invalid_storage_input
from kw_notice_mcp.storage_models import StoredNotice
from kw_notice_mcp.values import DUID, SourceURL

BODY_TTL_DAYS: Final = 30
MAX_SEARCH_QUERY_LENGTH: Final = 200
MAX_SEARCH_LIMIT: Final = 50
MAX_SEARCH_OFFSET: Final = 500
QUERY_FIELD: Final = "query"
LIMIT_FIELD: Final = "limit"
OFFSET_FIELD: Final = "offset"


def utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """Normalize a timestamp, treating a naive test value as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def timestamp(value: datetime) -> str:
    """Serialize an instant into a stable SQLite text value."""
    return as_utc(value).isoformat(timespec="microseconds")


def parse_timestamp(value: str) -> datetime:
    """Parse a stored ISO timestamp and normalize it to UTC."""
    return as_utc(datetime.fromisoformat(value))


def content_hash(summary: NoticeSummary, body: str | None) -> str:
    """Hash only the bounded, redacted notice projection persisted in SQLite."""
    fields = redact_human_fields(
        title=summary.title,
        category_name=summary.category_name,
        department=summary.department,
        body=body or "",
    )
    payload = "\x1f".join(
        (
            str(summary.duid),
            str(summary.category_id),
            fields.category_name,
            fields.title,
            summary.posted_date.isoformat(),
            summary.updated_date.isoformat(),
            fields.department,
            str(summary.source_url),
            fields.body,
            str(summary.attachments_present),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_search(query: str, limit: int, offset: int) -> None:
    """Reject malformed or unbounded search parameters before SQL construction."""
    if type(query) is not str:
        raise invalid_storage_input(QUERY_FIELD, "not-text")
    if len(query) > MAX_SEARCH_QUERY_LENGTH:
        raise invalid_storage_input(QUERY_FIELD, "oversized")
    if type(limit) is not int or limit < 1:
        raise invalid_storage_input(LIMIT_FIELD, str(limit))
    if type(offset) is not int or not 0 <= offset <= MAX_SEARCH_OFFSET:
        raise invalid_storage_input(OFFSET_FIELD, str(offset))


def safe_fts_query(query: str) -> str:
    """Quote each token so FTS operators and SQL punctuation remain inert text."""
    tokens = query.split()
    return " ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


SQLiteValue = str | int | None


class SQLiteCursor(Protocol):
    """Typed subset of the stdlib cursor used at the database boundary."""

    def fetchone(self) -> tuple[SQLiteValue, ...] | None:
        """Fetch one row."""
        ...

    def fetchall(self) -> list[tuple[SQLiteValue, ...]]:
        """Fetch all rows."""
        ...


class SQLiteReader(Protocol):
    """Typed subset of a sqlite connection's read operations."""

    def execute(
        self, sql: str, parameters: tuple[SQLiteValue, ...] = (), /
    ) -> SQLiteCursor:
        """Execute a parameterized read."""
        ...


def fetch_one(
    connection: SQLiteReader,
    sql: str,
    parameters: tuple[SQLiteValue, ...] = (),
) -> tuple[SQLiteValue, ...] | None:
    """Read one SQLite row through a typed boundary wrapper."""
    cursor = connection.execute(sql, parameters)
    row: tuple[SQLiteValue, ...] | None = cursor.fetchone()
    return row


def fetch_all(
    connection: SQLiteReader,
    sql: str,
    parameters: tuple[SQLiteValue, ...] = (),
) -> tuple[tuple[SQLiteValue, ...], ...]:
    """Read all SQLite rows through a typed boundary wrapper."""
    cursor = connection.execute(sql, parameters)
    rows: list[tuple[SQLiteValue, ...]] = cursor.fetchall()
    return tuple(rows)


def sqlite_int(value: SQLiteValue, field: str) -> int:
    """Parse an integer SQLite field at the storage boundary."""
    if type(value) is not int:
        raise invalid_storage_input(field, "invalid database value")
    return value


def row_to_notice(row: tuple[SQLiteValue, ...]) -> StoredNotice:
    """Convert a fixed-position SQLite row without exposing raw row mappings."""
    body_value = row[8]
    expiry_value = row[9]
    tombstone_value = row[13]
    body = body_value if isinstance(body_value, str) else None
    expiry = parse_timestamp(expiry_value) if isinstance(expiry_value, str) else None
    tombstone = (
        parse_timestamp(tombstone_value) if isinstance(tombstone_value, str) else None
    )
    return StoredNotice(
        duid=DUID(str(row[0])),
        category_id=CategoryId(str(row[1])),
        category_name=CategoryName(str(row[2])),
        title=str(row[3]),
        posted_date=date.fromisoformat(str(row[4])),
        updated_date=date.fromisoformat(str(row[5])),
        department=str(row[6]),
        source_url=SourceURL(str(row[7])),
        body=body,
        body_expires_at=expiry,
        content_hash=str(row[10]),
        attachments_present=bool(row[11]),
        collected_at=parse_timestamp(str(row[12])),
        tombstone_at=tombstone,
        source_status=str(row[14]),
    )


def detail_parts(notice: NoticeDetail) -> tuple[NoticeSummary, str]:
    """Return the typed summary/body pair accepted by the write boundary."""
    return notice.summary, notice.body
