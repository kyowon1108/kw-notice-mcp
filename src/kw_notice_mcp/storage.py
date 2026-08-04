"""Context-managed SQLite repository façade for KW notice storage."""

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import final, override

from kw_notice_mcp.storage_crawl import (
    finish_crawl,
    get_crawl,
    latest_crawl,
    latest_crawl_status,
    latest_successful_at,
    recover_crawl_runs,
    restart_checkpoint,
    start_crawl,
    update_crawl,
)
from kw_notice_mcp.storage_errors import (
    CrawlBusyError,
    FTS5UnavailableError,
    ReadOnlyStorageError,
    SchemaMigrationError,
    StorageError,
    StorageInputError,
    StorageUnavailableError,
)
from kw_notice_mcp.storage_models import CrawlRun, CrawlStatus, Freshness
from kw_notice_mcp.storage_notice import NoticeStorageMixin
from kw_notice_mcp.storage_read import StorageReadMixin
from kw_notice_mcp.storage_schema import (
    detect_fts5,
    migrate,
    require_fts5,
    schema_is_valid,
    table_names,
)
from kw_notice_mcp.storage_support import (
    as_utc,
    fetch_all,
    fetch_one,
    sqlite_int,
    utc_now,
)
from kw_notice_mcp.values import DUID

__all__ = [
    "CrawlBusyError",
    "FTS5UnavailableError",
    "Freshness",
    "ReadOnlyStorageError",
    "SQLiteNoticeStore",
    "SchemaMigrationError",
    "StorageError",
    "StorageInputError",
    "StorageRepository",
    "StorageUnavailableError",
    "open_read_only_storage",
    "open_storage",
]


@final
class SQLiteNoticeStore(NoticeStorageMixin, StorageReadMixin):
    """A repository whose connection lifetime is owned by a context manager."""

    def __init__(self, connection: sqlite3.Connection, *, read_only: bool) -> None:
        """Create a store around an already configured SQLite connection."""
        super().__init__(connection, read_only=read_only)
        self.fts5_available: bool = detect_fts5(connection)

    def __enter__(self) -> "SQLiteNoticeStore":
        """Return the repository while retaining its owned connection."""
        return self

    @override
    def _read_connection(self) -> sqlite3.Connection:
        """Expose the owned connection to the read-method mixin."""
        return self._connection

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the connection after the repository scope ends."""
        del exc_type, exc, traceback
        self._connection.close()

    def table_names(self) -> frozenset[str]:
        """Return initialized schema object names."""
        return table_names(self._connection)

    def revision_columns(self) -> tuple[str, ...]:
        """Return revision-table columns for schema checks."""
        rows = fetch_all(self._connection, "PRAGMA table_info(notice_revisions)")
        return tuple(str(row[1]) for row in rows)

    def query_only_enabled(self) -> bool:
        """Report whether SQLite query-only mode is enabled."""
        row = fetch_one(self._connection, "PRAGMA query_only")
        return row is not None and sqlite_int(row[0], "query_only") == 1

    def revision_count(self, duid: DUID) -> int:
        """Return the number of metadata-only revisions for one notice."""
        row = fetch_one(
            self._connection,
            "SELECT COUNT(*) FROM notice_revisions WHERE duid = ?",
            (duid,),
        )
        if row is None:
            reason = "revision count"
            raise StorageUnavailableError(reason)
        return sqlite_int(row[0], "revision count")

    def record_partial_index_absence(self, duids: tuple[DUID, ...]) -> None:
        """Make no deletion from a partial index; absence is not tombstone evidence."""
        del duids

    def freshness(self, duid: DUID, *, now: datetime | None = None) -> Freshness:
        """Calculate inclusive freshness bounds or expire without a success run."""
        notice = self.get(duid)
        successful_at = latest_successful_at(self._connection)
        if notice is None or successful_at is None:
            return Freshness.EXPIRED
        age = as_utc(now or utc_now()) - notice.collected_at
        if age <= timedelta(hours=24):
            return Freshness.FRESH
        if age <= timedelta(days=7):
            return Freshness.STALE
        return Freshness.EXPIRED

    def start_crawl(
        self, run_id: str, *, started_at: datetime | None = None
    ) -> CrawlRun:
        """Acquire a restartable crawl run lease."""
        return start_crawl(self._connection, run_id, started_at)

    def update_crawl(  # noqa: PLR0913
        self,
        run_id: str,
        *,
        checkpoint_page: int | None = None,
        pages_seen: int | None = None,
        detail_requests: int | None = None,
        index_requests: int | None = None,
        retry_count: int | None = None,
        block_reason: str | None = None,
        updated_at: datetime | None = None,
    ) -> CrawlRun:
        """Persist the latest crawl checkpoint and request counters."""
        return update_crawl(
            self._connection,
            run_id,
            checkpoint_page=checkpoint_page,
            pages_seen=pages_seen,
            detail_requests=detail_requests,
            index_requests=index_requests,
            retry_count=retry_count,
            block_reason=block_reason,
            updated_at=updated_at,
        )

    def finish_crawl(
        self,
        run_id: str,
        *,
        status: str | CrawlStatus,
        finished_at: datetime | None = None,
    ) -> CrawlRun:
        """Close a crawl run with a typed lifecycle status."""
        return finish_crawl(self._connection, run_id, status, finished_at)

    def recover_crawl_runs(
        self, *, now: datetime | None = None
    ) -> tuple[CrawlRun, ...]:
        """Recover runs older than fifteen minutes for collector restart."""
        return recover_crawl_runs(self._connection, now)

    def restart_checkpoint(self) -> int | None:
        """Return the newest interrupted page checkpoint."""
        return restart_checkpoint(self._connection)

    def get_crawl(self, run_id: str) -> CrawlRun | None:
        """Fetch one crawl row for restart/status consumers."""
        return get_crawl(self._connection, run_id)

    def latest_crawl_status(self) -> CrawlStatus | None:
        """Return the latest crawl status without exposing crawl write controls."""
        return latest_crawl_status(self._connection)

    def latest_crawl(self) -> CrawlRun | None:
        """Return the latest crawl row for local operational status."""
        return latest_crawl(self._connection)


def _connect(path: Path, *, read_only: bool) -> sqlite3.Connection:
    """Open a database with foreign keys and the requested access mode."""
    connection: sqlite3.Connection | None = None
    try:
        if read_only:
            connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(path)
        _ = connection.execute("PRAGMA foreign_keys = ON")
        if read_only:
            require_fts5(connection)
            if not schema_is_valid(connection):
                reason = "database schema"
                raise StorageUnavailableError(reason)
            _ = connection.execute("PRAGMA query_only = ON")
    except sqlite3.Error as error:
        if connection is not None:
            connection.close()
        reason = "open database"
        raise StorageUnavailableError(reason) from error
    else:
        return connection


def initialize_database(path: Path | str) -> None:
    """Create and migrate a database, failing with a typed FTS5 setup error."""
    connection = _connect(Path(path), read_only=False)
    try:
        require_fts5(connection)
        migrate(connection)
        connection.commit()
    except sqlite3.Error as error:
        connection.rollback()
        reason = "initialize database"
        raise StorageUnavailableError(reason) from error
    finally:
        connection.close()


@contextmanager
def open_storage(path: Path | str) -> Generator[SQLiteNoticeStore]:
    """Initialize a writable database and close it after the caller's scope."""
    database = Path(path)
    initialize_database(database)
    connection = _connect(database, read_only=False)
    try:
        yield SQLiteNoticeStore(connection, read_only=False)
    finally:
        connection.close()


@contextmanager
def open_read_only_storage(
    path: Path | str,
) -> Generator[SQLiteNoticeStore]:
    """Open an existing database through URI mode=ro and query-only mode."""
    connection = _connect(Path(path), read_only=True)
    try:
        yield SQLiteNoticeStore(connection, read_only=True)
    finally:
        connection.close()


StorageRepository = SQLiteNoticeStore
