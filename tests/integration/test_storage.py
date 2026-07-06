"""Integration tests for real sqlite3 storage connections."""

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from kw_notice_mcp.domain import CategoryId, CategoryName, NoticeDetail, NoticeSummary
from kw_notice_mcp.storage import (
    ReadOnlyStorageError,
    StorageUnavailableError,
    initialize_database,
    open_read_only_storage,
    open_storage,
)
from kw_notice_mcp.values import DUID, SourceURL


def _detail(duid: str, title: str = "Integration notice") -> NoticeDetail:
    return NoticeDetail(
        summary=NoticeSummary(
            duid=DUID(duid),
            title=title,
            category_id=CategoryId("general"),
            category_name=CategoryName("일반"),
            posted_date=date(2026, 7, 1),
            updated_date=date(2026, 7, 1),
            department="Department",
            source_url=SourceURL(
                f"https://www.kw.ac.kr/ko/life/notice.jsp?BoardMode=view&DUID={duid}"
            ),
            attachments_present=False,
            pinned=False,
        ),
        body="integration body",
    )


def test_read_only_connection_rejects_write(tmp_path: Path) -> None:
    """Given an initialized DB, the MCP storage factory rejects writes by type."""
    database = tmp_path / "notices.sqlite3"
    with open_storage(database) as store:
        _ = store.save_detail(_detail("1001"))

    with open_read_only_storage(database) as store:
        assert store.query_only_enabled() is True
        with pytest.raises(ReadOnlyStorageError):
            _ = store.save_detail(_detail("1002"))
        notice = store.get(DUID("1001"))
        assert notice is not None
        assert notice.title == "Integration notice"


def test_same_hash_is_idempotent(tmp_path: Path) -> None:
    """Given duplicate ingestion, exactly one notice and no revision exist."""
    database = tmp_path / "notices.sqlite3"
    with open_storage(database) as store:
        _ = store.save_detail(_detail("1001"))
        _ = store.save_detail(_detail("1001"))

        assert len(store.search()) == 1
        assert store.revision_count(DUID("1001")) == 0


def test_changed_hash_creates_one_revision(tmp_path: Path) -> None:
    """Given one changed body, integration storage records exactly one revision."""
    database = tmp_path / "notices.sqlite3"
    with open_storage(database) as store:
        _ = store.save_detail(_detail("1001", title="before"))
        _ = store.save_detail(_detail("1001", title="after"))
        _ = store.save_detail(_detail("1001", title="after"))

        assert store.revision_count(DUID("1001")) == 1


def test_locked_database_rejects_setup_with_typed_error(tmp_path: Path) -> None:
    """Given an exclusive SQLite lock, setup reports unavailable storage."""
    database = tmp_path / "notices.sqlite3"
    initialize_database(database)
    connection = sqlite3.connect(database, timeout=0.1)
    try:
        _ = connection.execute("BEGIN EXCLUSIVE")
        with pytest.raises(StorageUnavailableError):
            initialize_database(database)
    finally:
        connection.rollback()
        connection.close()
