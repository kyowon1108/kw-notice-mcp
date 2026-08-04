"""FULL collector transaction and failure-accounting regression tests."""

import sqlite3
from pathlib import Path

import anyio
import pytest

from kw_notice_mcp import storage_notice
from kw_notice_mcp.collector import CollectorConfig, CollectStatus
from kw_notice_mcp.domain import NoticeDetail
from kw_notice_mcp.parser import parse_detail_html
from kw_notice_mcp.storage_errors import StorageError
from kw_notice_mcp.storage_models import CrawlStatus
from kw_notice_mcp.values import DUID

from .collector_full_support import detail_body, full_wire
from .collector_test_support import (
    FIXTURES,
    FakeClock,
    FakeSleeper,
    collector,
    response,
)


def test_full_batch_rolls_back_detail_revision_fts_and_body_changes_on_later_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Given two staged details, a later storage failure leaves the DB unchanged."""
    first_detail = parse_detail_html(
        (FIXTURES / "notice_detail_minimal.html").read_text(encoding="utf-8"),
        "1001",
    ).notice
    second_detail = parse_detail_html(
        (FIXTURES / "notice_detail_minimal.html").read_text(encoding="utf-8"),
        "1002",
    ).notice
    assert first_detail is not None
    assert second_detail is not None
    wire = full_wire(detail_body(), detail_body())
    with collector(
        tmp_path / "db.sqlite3", wire, FakeSleeper(), FakeClock()
    ) as running:
        _ = running.store.save_detail(NoticeDetail(first_detail.summary, "old one"))
        _ = running.store.save_detail(NoticeDetail(second_detail.summary, "old two"))
        before = tuple(
            (
                running.store.get(duid),
                running.store.revision_count(duid),
                running.store.search(token),
            )
            for duid, token in (
                (DUID("1001"), "old one"),
                (DUID("1002"), "old two"),
            )
        )

        original_sync_fts = storage_notice.sync_fts
        calls = 0

        def fail_on_second_sync(connection: sqlite3.Connection, duid: DUID) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise StorageError
            original_sync_fts(connection, duid)

        monkeypatch.setattr(storage_notice, "sync_fts", fail_on_second_sync)
        result = anyio.run(
            lambda: running.run(
                run_id="full-detail-failure",
                config=CollectorConfig(max_detail_requests=2),
            )
        )
        crawl = running.store.get_crawl("full-detail-failure")
        after = tuple(
            (
                running.store.get(duid),
                running.store.revision_count(duid),
                running.store.search(token),
            )
            for duid, token in (
                (DUID("1001"), "old one"),
                (DUID("1002"), "old two"),
            )
        )

        assert after == before

    assert result.status is CollectStatus.BLOCKED
    assert result.reason == "collector_failure"
    assert result.wire_requests == 4
    assert result.retry_count == 0
    assert crawl is not None
    assert crawl.status is CrawlStatus.FAILED
    assert crawl.finished_at is not None
    assert crawl.pages_seen == 1
    assert crawl.detail_requests == 2
    assert crawl.index_requests == 4
    assert crawl.retry_count == 0


def test_full_batch_rolls_back_prior_tombstone_when_later_tombstone_fails(
    tmp_path: Path,
) -> None:
    """Given two explicit detail 404s, a later tombstone failure rolls both back."""
    first_detail = parse_detail_html(
        (FIXTURES / "notice_detail_minimal.html").read_text(encoding="utf-8"),
        "1001",
    ).notice
    second_detail = parse_detail_html(
        (FIXTURES / "notice_detail_minimal.html").read_text(encoding="utf-8"),
        "1002",
    ).notice
    assert first_detail is not None
    assert second_detail is not None
    wire = full_wire(
        response(404, b"", **{"content-type": "text/html"}),
        response(404, b"", **{"content-type": "text/html"}),
    )
    with collector(
        tmp_path / "db.sqlite3", wire, FakeSleeper(), FakeClock()
    ) as running:
        _ = running.store.save_detail(first_detail)
        _ = running.store.save_detail(
            NoticeDetail(second_detail.summary, "second body")
        )
        trigger_connection = sqlite3.connect(tmp_path / "db.sqlite3")
        try:
            _ = trigger_connection.execute(
                """
                CREATE TRIGGER fail_second_tombstone
                BEFORE UPDATE OF tombstone_at ON notices
                WHEN OLD.duid = '1002'
                BEGIN SELECT RAISE(ABORT, 'injected tombstone failure'); END;
                """
            )
            trigger_connection.commit()
        finally:
            trigger_connection.close()
        result = anyio.run(
            lambda: running.run(
                run_id="full-tombstone-failure",
                config=CollectorConfig(max_detail_requests=2),
            )
        )
        first = running.store.get(DUID("1001"))
        second = running.store.get(DUID("1002"))
        crawl = running.store.get_crawl("full-tombstone-failure")

    assert result.reason == "collector_failure"
    assert first is not None
    assert second is not None
    assert first.tombstone_at is None
    assert second.tombstone_at is None
    assert crawl is not None
    assert crawl.status is CrawlStatus.FAILED
    assert crawl.index_requests == 4
    assert crawl.detail_requests == 2


def test_full_batch_applies_details_and_tombstones_atomically_on_success(
    tmp_path: Path,
) -> None:
    """Given a valid FULL page, all staged detail and tombstone changes commit once."""
    first_detail = parse_detail_html(
        (FIXTURES / "notice_detail_minimal.html").read_text(encoding="utf-8"),
        "1001",
    ).notice
    second_detail = parse_detail_html(
        (FIXTURES / "notice_detail_minimal.html").read_text(encoding="utf-8"),
        "1002",
    ).notice
    assert first_detail is not None
    assert second_detail is not None
    wire = full_wire(detail_body(), response(404, b"", **{"content-type": "text/html"}))
    with collector(
        tmp_path / "db.sqlite3", wire, FakeSleeper(), FakeClock()
    ) as running:
        _ = running.store.save_detail(second_detail)
        result = anyio.run(
            lambda: running.run(
                run_id="full-success",
                config=CollectorConfig(max_detail_requests=2),
            )
        )
        first = running.store.get(DUID("1001"))
        second = running.store.get(DUID("1002"))
        crawl = running.store.get_crawl("full-success")

    assert result.status is CollectStatus.SUCCESS
    assert result.wire_requests == 4
    assert first is not None
    assert first.body is not None
    assert second is not None
    assert second.tombstone_at is not None
    assert crawl is not None
    assert crawl.status is CrawlStatus.SUCCESS
    assert crawl.pages_seen == 1
    assert crawl.detail_requests == 2
