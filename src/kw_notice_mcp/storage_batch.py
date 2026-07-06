"""Atomic publication helper for FULL collector detail batches."""

import sqlite3
from collections.abc import Callable, Sequence
from datetime import datetime

from kw_notice_mcp.domain import NoticeDetail, NoticeSummary
from kw_notice_mcp.storage_errors import StorageUnavailableError, write_storage_error
from kw_notice_mcp.storage_models import StoredNotice
from kw_notice_mcp.storage_support import as_utc, timestamp, utc_now
from kw_notice_mcp.values import DUID


def apply_detail_batch(  # noqa: PLR0913
    connection: sqlite3.Connection,
    read_only: bool,
    notices: Sequence[NoticeDetail],
    tombstones: Sequence[DUID],
    upsert_one: Callable[[NoticeSummary, str | None, datetime], None],
    tombstone_one: Callable[[DUID, str], None],
    get_notice: Callable[[DUID], StoredNotice | None],
    *,
    collected_at: datetime | None = None,
) -> tuple[StoredNotice, ...]:
    """Commit all detail, revision, body, FTS, and tombstone mutations together."""
    collected = as_utc(collected_at or utc_now())
    committed = False
    try:
        _ = connection.execute("BEGIN")
        for notice in notices:
            upsert_one(notice.summary, notice.body, collected)
        tombstone_at = timestamp(collected)
        for duid in tombstones:
            tombstone_one(duid, tombstone_at)
        connection.commit()
        committed = True
    except sqlite3.OperationalError as error:
        raise write_storage_error(error, "batch detail", read_only) from error
    except sqlite3.IntegrityError as error:
        reason = "batch detail integrity"
        raise StorageUnavailableError(reason) from error
    finally:
        if not committed:
            connection.rollback()
    return tuple(
        notice
        for detail in notices
        if (notice := get_notice(detail.summary.duid)) is not None
    )
