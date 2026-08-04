"""Tombstone and body-retention operations for notice storage."""

import sqlite3
from datetime import datetime

from kw_notice_mcp.storage_body import sync_fts
from kw_notice_mcp.storage_errors import write_storage_error
from kw_notice_mcp.storage_support import fetch_all, timestamp, utc_now
from kw_notice_mcp.values import DUID


class NoticeRetentionMixin:
    """Repository methods for explicit tombstones and expiring bodies."""

    _connection: sqlite3.Connection
    _read_only: bool

    def __init__(self, connection: sqlite3.Connection, *, read_only: bool) -> None:
        """Initialize the storage boundary shared by retention operations."""
        self._connection = connection
        self._read_only = read_only

    def mark_detail_404(
        self, duid: DUID, *, tombstone_at: datetime | None = None
    ) -> None:
        """Tombstone a known notice only after an explicit detail 404."""
        when = timestamp(tombstone_at or utc_now())
        try:
            _ = self._connection.execute("BEGIN")
            self._mark_detail_404_one(duid, when)
            self._connection.commit()
        except sqlite3.OperationalError as error:
            self._connection.rollback()
            raise write_storage_error(
                error, "tombstone notice", self._read_only
            ) from error

    def _mark_detail_404_one(self, duid: DUID, when: str) -> None:
        """Apply one tombstone inside a caller-owned transaction."""
        _ = self._connection.execute(
            "UPDATE notices SET tombstone_at = ?, source_status = ? WHERE duid = ?",
            (when, "tombstoned", duid),
        )
        _ = self._connection.execute("DELETE FROM notices_fts WHERE duid = ?", (duid,))

    def cleanup_expired_bodies(
        self, *, now: datetime | None = None
    ) -> tuple[DUID, ...]:
        """Null expired bodies and rebuild affected FTS rows atomically."""
        current = timestamp(now or utc_now())
        duids: tuple[DUID, ...] = ()
        try:
            _ = self._connection.execute("BEGIN")
            rows = fetch_all(
                self._connection,
                """
                SELECT duid FROM notices
                WHERE body IS NOT NULL AND body_expires_at <= ?
                """,
                (current,),
            )
            duids = tuple(DUID(str(row[0])) for row in rows)
            for duid in duids:
                _ = self._connection.execute(
                    """
                    UPDATE notices SET body = NULL, body_expires_at = NULL
                    WHERE duid = ?
                    """,
                    (duid,),
                )
                sync_fts(self._connection, duid)
            self._connection.commit()
        except sqlite3.OperationalError as error:
            self._connection.rollback()
            raise write_storage_error(error, "body cleanup", self._read_only) from error
        return duids
