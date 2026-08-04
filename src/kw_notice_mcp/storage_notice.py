"""Notice-row writes, FTS synchronization, retention, and tombstones."""

import sqlite3
from collections.abc import Sequence
from datetime import datetime, timedelta
from functools import partial

from kw_notice_mcp.domain import NoticeDetail, NoticeSummary
from kw_notice_mcp.redaction import RedactedHumanFields, redact_human_fields
from kw_notice_mcp.storage_batch import apply_detail_batch
from kw_notice_mcp.storage_body import sync_fts
from kw_notice_mcp.storage_errors import (
    StorageUnavailableError,
    write_storage_error,
)
from kw_notice_mcp.storage_models import StoredNotice
from kw_notice_mcp.storage_retention import NoticeRetentionMixin
from kw_notice_mcp.storage_sql import (
    NOTICE_SELECT_SQL,
    NOTICE_UPSERT_SQL,
    REVISION_INSERT_SQL,
)
from kw_notice_mcp.storage_support import (
    BODY_TTL_DAYS,
    SQLiteValue,
    as_utc,
    content_hash,
    fetch_one,
    row_to_notice,
    timestamp,
    utc_now,
)
from kw_notice_mcp.values import DUID

__all__ = ["NoticeStorageMixin", "sync_fts"]


class NoticeStorageMixin(NoticeRetentionMixin):
    """Repository methods for notice data, shared by the public store façade."""

    _connection: sqlite3.Connection
    _read_only: bool

    def __init__(self, connection: sqlite3.Connection, *, read_only: bool) -> None:
        """Initialize state supplied by the public repository façade."""
        super().__init__(connection, read_only=read_only)

    def get(self, duid: DUID) -> StoredNotice | None:
        """Fetch one notice by stable DUID."""
        row = fetch_one(self._connection, NOTICE_SELECT_SQL, (duid,))
        return row_to_notice(row) if row is not None else None

    def save_summary(
        self, notice: NoticeSummary, *, collected_at: datetime | None = None
    ) -> StoredNotice:
        """Upsert list metadata while preserving any retained detail body."""
        existing = self.get(notice.duid)
        body = existing.body if existing is not None else None
        return self._upsert(
            notice, body, collected_at, preserve_tombstone=True, preserve_body=True
        )

    def save_metadata(
        self, notice: NoticeSummary, *, collected_at: datetime | None = None
    ) -> StoredNotice:
        """Upsert metadata and clear any retained body in the same transaction."""
        return self._upsert(
            notice,
            None,
            collected_at,
            preserve_tombstone=True,
            preserve_body=False,
        )

    def save_metadata_batch(
        self,
        notices: Sequence[NoticeSummary],
        *,
        collected_at: datetime | None = None,
    ) -> tuple[StoredNotice, ...]:
        """Atomically ingest one metadata page and purge retained bodies."""
        collected = as_utc(collected_at or utc_now())
        committed = False
        try:
            _ = self._connection.execute("BEGIN")
            for summary in notices:
                self._upsert_one(
                    summary,
                    None,
                    collected,
                    preserve_tombstone=True,
                    preserve_body=False,
                )
            self._connection.commit()
            committed = True
        except sqlite3.OperationalError as error:
            raise write_storage_error(
                error, "batch metadata", self._read_only
            ) from error
        except sqlite3.IntegrityError as error:
            reason = "batch metadata integrity"
            raise StorageUnavailableError(reason) from error
        finally:
            if not committed:
                self._connection.rollback()
        return tuple(
            notice
            for summary in notices
            if (notice := self.get(summary.duid)) is not None
        )

    def save_detail(
        self, notice: NoticeDetail, *, collected_at: datetime | None = None
    ) -> StoredNotice:
        """Upsert detail metadata/body and refresh its thirty-day retention window."""
        return self._upsert(
            notice.summary,
            notice.body,
            collected_at,
            preserve_tombstone=False,
            preserve_body=False,
        )

    def save_detail_batch(
        self,
        notices: Sequence[NoticeDetail],
        tombstones: Sequence[DUID] = (),
        *,
        collected_at: datetime | None = None,
    ) -> tuple[StoredNotice, ...]:
        """Atomically publish all FULL details and explicit detail 404 tombstones."""
        upsert = partial(
            self._upsert_one, preserve_tombstone=False, preserve_body=False
        )
        tombstone = self._mark_detail_404_one

        return apply_detail_batch(
            self._connection,
            self._read_only,
            notices,
            tombstones,
            upsert,
            tombstone,
            self.get,
            collected_at=collected_at,
        )

    def _upsert(
        self,
        summary: NoticeSummary,
        body: str | None,
        collected_at: datetime | None,
        *,
        preserve_tombstone: bool,
        preserve_body: bool,
    ) -> StoredNotice:
        """Atomically upsert one bounded notice and its FTS projection."""
        committed = False
        try:
            _ = self._connection.execute("BEGIN")
            self._upsert_one(
                summary,
                body,
                as_utc(collected_at or utc_now()),
                preserve_tombstone=preserve_tombstone,
                preserve_body=preserve_body,
            )
            self._connection.commit()
            committed = True
        except sqlite3.OperationalError as error:
            raise write_storage_error(
                error, "upsert notice", self._read_only
            ) from error
        except sqlite3.IntegrityError as error:
            reason = "notice integrity"
            raise StorageUnavailableError(reason) from error
        finally:
            if not committed:
                self._connection.rollback()
        result = self.get(summary.duid)
        if result is None:
            reason = "notice read-back"
            raise StorageUnavailableError(reason)
        return result

    def _upsert_one(
        self,
        summary: NoticeSummary,
        body: str | None,
        collected: datetime,
        *,
        preserve_tombstone: bool,
        preserve_body: bool,
    ) -> None:
        """Apply one notice mutation inside the caller-owned transaction."""
        fields = redact_human_fields(
            title=summary.title,
            category_name=summary.category_name,
            department=summary.department,
            body=body or "",
        )
        existing = self.get(summary.duid)
        old_body = existing.body if existing is not None else None
        actual_body = (
            fields.body if body is not None else old_body if preserve_body else None
        )
        new_hash = content_hash(summary, actual_body)
        expiry = (
            timestamp(collected + timedelta(days=BODY_TTL_DAYS))
            if body is not None
            else timestamp(existing.body_expires_at)
            if preserve_body and existing and existing.body_expires_at
            else None
        )
        tombstone = (
            timestamp(existing.tombstone_at)
            if preserve_tombstone and existing and existing.tombstone_at
            else None
        )
        status = existing.source_status if tombstone and existing else "active"
        params: tuple[SQLiteValue, ...] = (
            str(summary.duid),
            str(summary.category_id),
            fields.category_name,
            fields.title,
            summary.posted_date.isoformat(),
            summary.updated_date.isoformat(),
            fields.department,
            str(summary.source_url),
            actual_body,
            expiry,
            new_hash,
            int(summary.attachments_present),
            timestamp(collected),
            tombstone,
            status,
        )
        if existing is not None and existing.content_hash != new_hash:
            self._insert_revision(summary, fields, collected, new_hash)
        _ = self._connection.execute(NOTICE_UPSERT_SQL, params)
        sync_fts(self._connection, summary.duid)

    def _insert_revision(
        self,
        summary: NoticeSummary,
        fields: RedactedHumanFields,
        collected: datetime,
        new_hash: str,
    ) -> None:
        _ = self._connection.execute(
            REVISION_INSERT_SQL,
            (
                str(summary.duid),
                new_hash,
                str(summary.category_id),
                fields.category_name,
                fields.title,
                summary.posted_date.isoformat(),
                summary.updated_date.isoformat(),
                fields.department,
                str(summary.source_url),
                int(summary.attachments_present),
                timestamp(collected),
                timestamp(collected),
            ),
        )
