"""Unit tests for deterministic SQLite storage behavior."""

import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

import kw_notice_mcp.storage as storage_module
from kw_notice_mcp.domain import CategoryId, CategoryName, NoticeDetail, NoticeSummary
from kw_notice_mcp.storage import (
    Freshness,
    FTS5UnavailableError,
    SchemaMigrationError,
    StorageInputError,
    StorageUnavailableError,
    initialize_database,
    open_read_only_storage,
    open_storage,
)
from kw_notice_mcp.storage_models import StoredNotice
from kw_notice_mcp.values import DUID, SourceURL


def _summary(
    duid: str, *, updated: date | None = None, title: str = "Notice"
) -> NoticeSummary:
    actual_updated = updated or date(2026, 8, 1)
    return NoticeSummary(
        duid=DUID(duid),
        title=title,
        category_id=CategoryId("general"),
        category_name=CategoryName("일반"),
        posted_date=date(2026, 7, 1),
        updated_date=actual_updated,
        department="Student Affairs",
        source_url=SourceURL(
            f"https://www.kw.ac.kr/ko/life/notice.jsp?BoardMode=view&DUID={duid}"
        ),
        attachments_present=False,
        pinned=False,
    )


def _detail(duid: str, *, body: str = "Searchable body") -> NoticeDetail:
    return NoticeDetail(summary=_summary(duid), body=body)


def _require_notice(notice: StoredNotice | None) -> StoredNotice:
    assert notice is not None
    return notice


def test_schema_creation_and_fts_detection(tmp_path: Path) -> None:
    """Given a new path, setup creates versioned tables and an FTS5 index."""
    database = tmp_path / "notices.sqlite3"

    with open_storage(database) as store:
        assert store.fts5_available is True
        tables = store.table_names()

    assert {"schema_version", "notices", "notice_revisions", "crawl_runs"}.issubset(
        tables
    )
    assert "notices_fts" in tables


def test_revision_schema_excludes_body_text(tmp_path: Path) -> None:
    """Given changed redacted details, the revision table has no body column."""
    database = tmp_path / "notices.sqlite3"
    with open_storage(database) as store:
        _ = store.save_detail(_detail("1001", body="private body"))
        _ = store.save_detail(_detail("1001", body="changed body"))
        assert "body" not in store.revision_columns()


def test_fts5_setup_failure_is_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a runtime without FTS5, setup stops with a typed capability error."""

    def fail_fts5(connection: sqlite3.Connection) -> None:
        del connection
        raise FTS5UnavailableError

    monkeypatch.setattr(storage_module, "require_fts5", fail_fts5)

    with pytest.raises(FTS5UnavailableError):
        initialize_database(tmp_path / "missing-fts.sqlite3")


def test_corrupt_schema_version_is_typed(tmp_path: Path) -> None:
    """Given a future schema version, migration refuses unsafe downgrade."""
    database = tmp_path / "future.sqlite3"
    connection = sqlite3.connect(database)
    try:
        _ = connection.execute("CREATE TABLE schema_version(version INTEGER NOT NULL)")
        _ = connection.execute("INSERT INTO schema_version(version) VALUES (99)")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(SchemaMigrationError):
        initialize_database(database)


def test_missing_database_read_only_open_is_typed(tmp_path: Path) -> None:
    """Given an absent path, read-only setup returns a storage error, not a write."""
    with (
        pytest.raises(StorageUnavailableError),
        open_read_only_storage(tmp_path / "absent.sqlite3"),
    ):
        pass


def test_same_hash_is_idempotent_and_changed_hash_creates_one_revision(
    tmp_path: Path,
) -> None:
    """Given one notice, duplicate detail ingestion is stable and one change revises."""
    with open_storage(tmp_path / "notices.sqlite3") as store:
        _ = store.save_detail(_detail("1001", body="same"))
        _ = store.save_detail(_detail("1001", body="same"))
        assert store.revision_count(DUID("1001")) == 0

        _ = store.save_detail(_detail("1001", body="changed"))
        _ = store.save_detail(_detail("1001", body="changed"))

        assert store.revision_count(DUID("1001")) == 1
        assert _require_notice(store.get(DUID("1001"))).body == "changed"


def test_search_filters_and_stable_tie_break_are_bounded(tmp_path: Path) -> None:
    """Given matching notices, FTS relevance and tie ordering are deterministic."""
    with open_storage(tmp_path / "notices.sqlite3") as store:
        for number in range(1, 54):
            _ = store.save_detail(
                _detail(
                    str(number),
                    body="target" if number in {2, 3} else "other",
                )
            )
        _ = store.save_summary(_summary("2000", title="Other category"))

        results = store.search("target", limit=50)
        assert tuple(item.duid for item in results) == (DUID("3"), DUID("2"))
        assert len(store.search(limit=500)) == 50
        assert store.search(category_id="general", updated_from=date(2026, 8, 1))
        assert not store.search(category_id="academic")

        assert store.search('"unterminated', limit=1) == ()
        with pytest.raises(StorageInputError):
            _ = store.search("x" * 201)


def test_ttl_cleanup_removes_body_and_fts_tokens(tmp_path: Path) -> None:
    """Given an expired body, cleanup nulls it and removes its searchable tokens."""
    collected = datetime(2026, 8, 1, tzinfo=UTC)
    with open_storage(tmp_path / "notices.sqlite3") as store:
        _ = store.save_detail(
            _detail("1001", body="ephemeral_token"), collected_at=collected
        )

        removed = store.cleanup_expired_bodies(
            now=collected + timedelta(days=30, seconds=1)
        )

        assert removed == (DUID("1001"),)
        assert _require_notice(store.get(DUID("1001"))).body is None
        assert store.search("ephemeral_token") == ()


def test_detail_404_tombstones_but_index_absence_does_not(tmp_path: Path) -> None:
    """Given partial crawl information, only explicit detail 404 tombstones."""
    with open_storage(tmp_path / "notices.sqlite3") as store:
        _ = store.save_detail(_detail("1001"))
        store.record_partial_index_absence((DUID("1001"),))
        assert _require_notice(store.get(DUID("1001"))).tombstone_at is None

        store.mark_detail_404(DUID("1001"))
        notice = _require_notice(store.get(DUID("1001")))
        assert notice.tombstone_at is not None
        assert notice.source_status == "tombstoned"
        assert store.search() == ()


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (timedelta(hours=24), Freshness.FRESH),
        (timedelta(hours=24, seconds=1), Freshness.STALE),
        (timedelta(days=7), Freshness.STALE),
        (timedelta(days=7, seconds=1), Freshness.EXPIRED),
    ],
)
def test_freshness_boundaries(
    tmp_path: Path, age: timedelta, expected: Freshness
) -> None:
    """Given a successful run, freshness uses inclusive 24-hour and 7-day bounds."""
    now = datetime(2026, 8, 10, tzinfo=UTC)
    with open_storage(tmp_path / "notices.sqlite3") as store:
        _ = store.save_detail(_detail("1001"), collected_at=now - age)
        _ = store.start_crawl("run-1", started_at=now - timedelta(minutes=1))
        _ = store.finish_crawl("run-1", status="success", finished_at=now)
        assert store.freshness(DUID("1001"), now=now) is expected


def test_no_successful_run_is_expired(tmp_path: Path) -> None:
    """Given only interrupted crawl state, a notice is expired regardless of age."""
    now = datetime(2026, 8, 10, tzinfo=UTC)
    with open_storage(tmp_path / "notices.sqlite3") as store:
        _ = store.save_detail(_detail("1001"), collected_at=now)
        _ = store.start_crawl("run-1", started_at=now)
        _ = store.finish_crawl("run-1", status="interrupted", finished_at=now)
        assert store.freshness(DUID("1001"), now=now) is Freshness.EXPIRED


def test_restart_recovery_marks_stale_run_and_returns_checkpoint(
    tmp_path: Path,
) -> None:
    """Given an old running row, recovery interrupts it and exposes its checkpoint."""
    now = datetime(2026, 8, 10, tzinfo=UTC)
    with open_storage(tmp_path / "notices.sqlite3") as store:
        _ = store.start_crawl("run-1", started_at=now - timedelta(minutes=16))
        _ = store.update_crawl(
            "run-1", checkpoint_page=7, pages_seen=7, detail_requests=3
        )

        recovered = store.recover_crawl_runs(now=now)

        assert recovered[0].run_id == "run-1"
        assert recovered[0].status == "interrupted"
        assert recovered[0].checkpoint_page == 7
        assert store.restart_checkpoint() == 7


def test_search_quotes_operators_and_injection_are_harmless(tmp_path: Path) -> None:
    """Given FTS operators and SQL punctuation, search returns no injected rows."""
    with open_storage(tmp_path / "notices.sqlite3") as store:
        _ = store.save_detail(_detail("1001", body="ordinary"))

        for query in ('" OR 1=1 --', "title:*", "foo NEAR/2 bar", "'"):
            assert store.search(query) == ()
