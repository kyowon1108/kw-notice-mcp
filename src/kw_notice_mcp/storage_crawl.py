"""Crawl-run persistence and restart recovery queries."""

import sqlite3
from datetime import datetime, timedelta
from typing import NoReturn

from kw_notice_mcp.storage_errors import (
    CrawlBusyError,
    invalid_storage_input,
)
from kw_notice_mcp.storage_models import CrawlRun, CrawlStatus
from kw_notice_mcp.storage_support import (
    SQLiteValue,
    as_utc,
    fetch_all,
    fetch_one,
    sqlite_int,
    timestamp,
    utc_now,
)

RUN_ID_FIELD = "run_id"
STATUS_FIELD = "status"

_CRAWL_SELECT_SQL = """
SELECT run_id, status, checkpoint_page, pages_seen, detail_requests,
       index_requests, retry_count, block_reason, started_at, updated_at,
       finished_at
FROM crawl_runs WHERE run_id = ?
"""
_CRAWL_UPDATE_SQL = """
UPDATE crawl_runs
SET checkpoint_page = ?, pages_seen = ?, detail_requests = ?,
    index_requests = ?, retry_count = ?, block_reason = ?, updated_at = ?
WHERE run_id = ?
"""


def _raise_busy(run_id: str) -> NoReturn:
    """Raise the busy result outside the transaction try block."""
    raise CrawlBusyError(run_id)


def _crawl_row(row: tuple[SQLiteValue, ...]) -> CrawlRun:
    """Convert the fixed crawl_runs column order to an immutable value."""
    finished = row[10]
    return CrawlRun(
        run_id=str(row[0]),
        status=CrawlStatus(str(row[1])),
        checkpoint_page=sqlite_int(row[2], "checkpoint_page"),
        pages_seen=sqlite_int(row[3], "pages_seen"),
        detail_requests=sqlite_int(row[4], "detail_requests"),
        index_requests=sqlite_int(row[5], "index_requests"),
        retry_count=sqlite_int(row[6], "retry_count"),
        block_reason=str(row[7]) if row[7] is not None else None,
        started_at=as_utc(datetime.fromisoformat(str(row[8]))),
        updated_at=as_utc(datetime.fromisoformat(str(row[9]))),
        finished_at=(
            as_utc(datetime.fromisoformat(str(finished)))
            if finished is not None
            else None
        ),
    )


def get_crawl(connection: sqlite3.Connection, run_id: str) -> CrawlRun | None:
    """Read one crawl row by its stable run identifier."""
    row = fetch_one(
        connection,
        _CRAWL_SELECT_SQL,
        (run_id,),
    )
    return _crawl_row(row) if row is not None else None


def start_crawl(
    connection: sqlite3.Connection,
    run_id: str,
    started_at: datetime | None,
) -> CrawlRun:
    """Acquire the crawl lease, interrupting only runs older than fifteen minutes."""
    start = as_utc(started_at or utc_now())
    now_text = timestamp(start)
    cutoff = timestamp(start - timedelta(minutes=15))
    _ = connection.execute("BEGIN IMMEDIATE")
    active: tuple[SQLiteValue, ...] | None = None
    try:
        _ = connection.execute(
            """
            UPDATE crawl_runs SET status = ?, updated_at = ?, finished_at = ?
            WHERE status = ? AND updated_at < ?
            """,
            (CrawlStatus.INTERRUPTED, now_text, now_text, CrawlStatus.RUNNING, cutoff),
        )
        active = fetch_one(
            connection,
            "SELECT run_id FROM crawl_runs WHERE status = ? LIMIT 1",
            (CrawlStatus.RUNNING,),
        )
        if active is not None:
            _raise_busy(str(active[0]))
        _ = connection.execute(
            """
            INSERT INTO crawl_runs(run_id, status, started_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (run_id, CrawlStatus.RUNNING, now_text, now_text),
        )
        connection.commit()
    except CrawlBusyError:
        connection.rollback()
        raise
    except sqlite3.IntegrityError:
        connection.rollback()
        raise invalid_storage_input(RUN_ID_FIELD, run_id) from None
    result = get_crawl(connection, run_id)
    if result is None:
        raise invalid_storage_input(RUN_ID_FIELD, run_id)
    return result


def update_crawl(  # noqa: PLR0913
    connection: sqlite3.Connection,
    run_id: str,
    *,
    checkpoint_page: int | None,
    pages_seen: int | None,
    detail_requests: int | None,
    index_requests: int | None,
    retry_count: int | None,
    block_reason: str | None,
    updated_at: datetime | None,
) -> CrawlRun:
    """Update restart counters using one parameterized transaction."""
    existing = get_crawl(connection, run_id)
    if existing is None:
        raise invalid_storage_input(RUN_ID_FIELD, run_id)
    values: tuple[SQLiteValue, ...] = (
        checkpoint_page if checkpoint_page is not None else existing.checkpoint_page,
        pages_seen if pages_seen is not None else existing.pages_seen,
        detail_requests if detail_requests is not None else existing.detail_requests,
        index_requests if index_requests is not None else existing.index_requests,
        retry_count if retry_count is not None else existing.retry_count,
        block_reason if block_reason is not None else existing.block_reason,
        timestamp(updated_at or utc_now()),
        run_id,
    )
    _ = connection.execute(_CRAWL_UPDATE_SQL, values)
    connection.commit()
    result = get_crawl(connection, run_id)
    if result is None:
        raise invalid_storage_input(RUN_ID_FIELD, run_id)
    return result


def finish_crawl(
    connection: sqlite3.Connection,
    run_id: str,
    status: str | CrawlStatus,
    finished_at: datetime | None,
) -> CrawlRun:
    """Close a crawl run with a typed lifecycle status."""
    try:
        final_status = CrawlStatus(status)
    except ValueError:
        raise invalid_storage_input(STATUS_FIELD, str(status)) from None
    if final_status is CrawlStatus.RUNNING:
        raise invalid_storage_input(STATUS_FIELD, str(status))
    finished = timestamp(finished_at or utc_now())
    _ = connection.execute(
        """
        UPDATE crawl_runs SET status = ?, updated_at = ?, finished_at = ?
        WHERE run_id = ?
        """,
        (final_status, finished, finished, run_id),
    )
    connection.commit()
    result = get_crawl(connection, run_id)
    if result is None:
        raise invalid_storage_input(RUN_ID_FIELD, run_id)
    return result


def recover_crawl_runs(
    connection: sqlite3.Connection, now: datetime | None
) -> tuple[CrawlRun, ...]:
    """Interrupt stale running rows and return the recovered checkpoints."""
    current = as_utc(now or utc_now())
    cutoff = timestamp(current - timedelta(minutes=15))
    current_text = timestamp(current)
    _ = connection.execute(
        """
        UPDATE crawl_runs SET status = ?, updated_at = ?, finished_at = ?
        WHERE status = ? AND updated_at < ?
        """,
        (
            CrawlStatus.INTERRUPTED,
            current_text,
            current_text,
            CrawlStatus.RUNNING,
            cutoff,
        ),
    )
    connection.commit()
    rows = fetch_all(
        connection,
        """
        SELECT run_id, status, checkpoint_page, pages_seen, detail_requests,
               index_requests, retry_count, block_reason, started_at, updated_at,
               finished_at
        FROM crawl_runs WHERE status = ? AND finished_at = ?
        ORDER BY updated_at DESC
        """,
        (CrawlStatus.INTERRUPTED, current_text),
    )
    return tuple(_crawl_row(row) for row in rows)


def restart_checkpoint(connection: sqlite3.Connection) -> int | None:
    """Return the newest interrupted crawl checkpoint, if one exists."""
    row = fetch_one(
        connection,
        """
        SELECT checkpoint_page FROM crawl_runs
        WHERE status = ? ORDER BY updated_at DESC LIMIT 1
        """,
        (CrawlStatus.INTERRUPTED,),
    )
    return sqlite_int(row[0], "checkpoint_page") if row is not None else None


def latest_successful_at(connection: sqlite3.Connection) -> datetime | None:
    """Return the most recent successful crawl completion timestamp."""
    row = fetch_one(
        connection,
        """
        SELECT finished_at FROM crawl_runs
        WHERE status = ? ORDER BY finished_at DESC LIMIT 1
        """,
        (CrawlStatus.SUCCESS,),
    )
    return as_utc(datetime.fromisoformat(str(row[0]))) if row is not None else None


def latest_crawl_status(connection: sqlite3.Connection) -> CrawlStatus | None:
    """Return the newest crawl status for MCP cache-blocked reporting."""
    row = fetch_one(
        connection,
        "SELECT status FROM crawl_runs ORDER BY updated_at DESC LIMIT 1",
    )
    return CrawlStatus(str(row[0])) if row is not None else None


def latest_crawl(connection: sqlite3.Connection) -> CrawlRun | None:
    """Return the newest crawl row for the human status command."""
    row = fetch_one(
        connection,
        """
        SELECT run_id, status, checkpoint_page, pages_seen, detail_requests,
               index_requests, retry_count, block_reason, started_at, updated_at,
               finished_at
        FROM crawl_runs ORDER BY updated_at DESC LIMIT 1
        """,
    )
    return _crawl_row(row) if row is not None else None
