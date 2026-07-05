"""Parameterized FTS5 and metadata search queries."""

import sqlite3
from datetime import date

from kw_notice_mcp.storage_models import StoredNotice
from kw_notice_mcp.storage_support import (
    MAX_SEARCH_LIMIT,
    SQLiteValue,
    fetch_all,
    row_to_notice,
    safe_fts_query,
    sqlite_int,
    validate_search,
)

_SEARCH_WITH_QUERY_SQL = """
SELECT n.duid, n.category_id, n.category_name, n.title, n.posted_date,
       n.updated_date, n.department, n.source_url, n.body, n.body_expires_at,
       n.content_hash, n.attachments_present, n.collected_at, n.tombstone_at,
       n.source_status
FROM notices AS n
JOIN notices_fts AS f ON f.duid = n.duid
WHERE n.tombstone_at IS NULL
  AND (? IS NULL OR n.category_id = ?)
  AND (? IS NULL OR n.updated_date >= ?)
  AND (? IS NULL OR n.updated_date <= ?)
  AND (? IS NULL OR n.posted_date >= ?)
  AND (? IS NULL OR n.posted_date <= ?)
  AND notices_fts MATCH ?
ORDER BY bm25(notices_fts) ASC, n.updated_date DESC, n.duid DESC
LIMIT ? OFFSET ?
"""
_SEARCH_WITHOUT_QUERY_SQL = """
SELECT n.duid, n.category_id, n.category_name, n.title, n.posted_date,
       n.updated_date, n.department, n.source_url, n.body, n.body_expires_at,
       n.content_hash, n.attachments_present, n.collected_at, n.tombstone_at,
       n.source_status
FROM notices AS n
WHERE n.tombstone_at IS NULL
  AND (? IS NULL OR n.category_id = ?)
  AND (? IS NULL OR n.updated_date >= ?)
  AND (? IS NULL OR n.updated_date <= ?)
  AND (? IS NULL OR n.posted_date >= ?)
  AND (? IS NULL OR n.posted_date <= ?)
ORDER BY n.updated_date DESC, n.duid DESC
LIMIT ? OFFSET ?
"""

_LATEST_SQL = """
SELECT n.duid, n.category_id, n.category_name, n.title, n.posted_date,
       n.updated_date, n.department, n.source_url, n.body, n.body_expires_at,
       n.content_hash, n.attachments_present, n.collected_at, n.tombstone_at,
       n.source_status
FROM notices AS n
WHERE n.tombstone_at IS NULL
  AND (? IS NULL OR n.category_id = ?)
ORDER BY n.posted_date DESC, n.duid DESC
LIMIT ? OFFSET ?
"""
_SEARCH_COUNT_SQL = """
SELECT COUNT(*)
FROM notices AS n JOIN notices_fts AS f ON f.duid = n.duid
WHERE n.tombstone_at IS NULL
  AND (? IS NULL OR n.category_id = ?)
  AND (? IS NULL OR n.posted_date >= ?)
  AND (? IS NULL OR n.posted_date <= ?)
  AND notices_fts MATCH ?
"""
_LATEST_COUNT_SQL = """
SELECT COUNT(*) FROM notices AS n
WHERE n.tombstone_at IS NULL AND (? IS NULL OR n.category_id = ?)
"""


def search_notices(  # noqa: PLR0913, PLR0917
    connection: sqlite3.Connection,
    query: str,
    category_id: str | None,
    updated_from: date | None,
    updated_to: date | None,
    limit: int,
    offset: int,
    posted_from: date | None = None,
    posted_to: date | None = None,
) -> tuple[StoredNotice, ...]:
    """Search active notices with bounded parameters and deterministic ordering."""
    validate_search(query, limit, offset)
    bounded_limit = min(limit, MAX_SEARCH_LIMIT)
    category = category_id
    lower = updated_from.isoformat() if updated_from is not None else None
    upper = updated_to.isoformat() if updated_to is not None else None
    posted_lower = posted_from.isoformat() if posted_from is not None else None
    posted_upper = posted_to.isoformat() if posted_to is not None else None
    if query.strip():
        sql = _SEARCH_WITH_QUERY_SQL
        parameters: tuple[SQLiteValue, ...] = (
            category,
            category,
            lower,
            lower,
            upper,
            upper,
            posted_lower,
            posted_lower,
            posted_upper,
            posted_upper,
            safe_fts_query(query),
            bounded_limit,
            offset,
        )
    else:
        sql = _SEARCH_WITHOUT_QUERY_SQL
        parameters = (
            category,
            category,
            lower,
            lower,
            upper,
            upper,
            posted_lower,
            posted_lower,
            posted_upper,
            posted_upper,
            bounded_limit,
            offset,
        )
    rows = fetch_all(connection, sql, tuple(parameters))
    return tuple(row_to_notice(tuple(row)) for row in rows)


def search_count(
    connection: sqlite3.Connection,
    query: str,
    category_id: str | None,
    posted_from: date | None,
    posted_to: date | None,
) -> int:
    """Count active FTS matches for exact pagination metadata."""
    validate_search(query, 1, 0)
    if not query.strip():
        return latest_count(connection, category_id, posted_from, posted_to)
    lower = posted_from.isoformat() if posted_from is not None else None
    upper = posted_to.isoformat() if posted_to is not None else None
    rows = fetch_all(
        connection,
        _SEARCH_COUNT_SQL,
        (category_id, category_id, lower, lower, upper, upper, safe_fts_query(query)),
    )
    return sqlite_int(rows[0][0], "search count") if rows else 0


def latest_notices(
    connection: sqlite3.Connection,
    category_id: str | None,
    limit: int,
    offset: int,
) -> tuple[StoredNotice, ...]:
    """Read active notices ordered by posted date descending."""
    validate_search("", limit, offset)
    rows = fetch_all(
        connection,
        _LATEST_SQL,
        (category_id, category_id, min(limit, MAX_SEARCH_LIMIT), offset),
    )
    return tuple(row_to_notice(tuple(row)) for row in rows)


def latest_count(
    connection: sqlite3.Connection,
    category_id: str | None,
    posted_from: date | None = None,
    posted_to: date | None = None,
) -> int:
    """Count active notices for pagination or an empty-query search."""
    lower = posted_from.isoformat() if posted_from is not None else None
    upper = posted_to.isoformat() if posted_to is not None else None
    if posted_from is None and posted_to is None:
        rows = fetch_all(connection, _LATEST_COUNT_SQL, (category_id, category_id))
    else:
        rows = fetch_all(
            connection,
            """
            SELECT COUNT(*) FROM notices AS n
            WHERE n.tombstone_at IS NULL
              AND (? IS NULL OR n.category_id = ?)
              AND (? IS NULL OR n.posted_date >= ?)
              AND (? IS NULL OR n.posted_date <= ?)
            """,
            (category_id, category_id, lower, lower, upper, upper),
        )
    return sqlite_int(rows[0][0], "latest count") if rows else 0


def category_counts(connection: sqlite3.Connection) -> tuple[tuple[str, int], ...]:
    """Return non-tombstoned counts keyed by stored category ID."""
    rows = fetch_all(
        connection,
        """
        SELECT category_id, COUNT(*) FROM notices
        WHERE tombstone_at IS NULL GROUP BY category_id
        """,
    )
    return tuple((str(row[0]), sqlite_int(row[1], "category count")) for row in rows)
