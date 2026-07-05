"""Unit tests for metadata-only collector behavior."""

import sqlite3
from pathlib import Path

import anyio
import pytest

from kw_notice_mcp import storage_notice
from kw_notice_mcp.collector import CollectionMode, CollectorConfig, CollectStatus
from kw_notice_mcp.domain import NoticeDetail
from kw_notice_mcp.parser import parse_detail_html, parse_list_html
from kw_notice_mcp.storage_body import sync_fts as original_sync_fts
from kw_notice_mcp.storage_errors import StorageError
from kw_notice_mcp.storage_models import CrawlStatus
from kw_notice_mcp.values import DUID

from .collector_test_support import (
    FIXTURES,
    FakeClock,
    FakeSleeper,
    FakeWire,
    collector,
    response,
)

PAGE_URL = (
    "https://www.kw.ac.kr/ko/life/notice.jsp?srCategoryId=&mode=list&"
    "searchKey=1&searchVal=&tpage=1"
)


def test_metadata_only_requests_only_first_index_then_purges_body_and_fts(
    tmp_path: Path,
) -> None:
    """Given metadata-only mode, persist list fields and remove retained body tokens."""
    list_html = (FIXTURES / "board_list_minimal.html").read_bytes()
    detail = parse_detail_html(
        (FIXTURES / "notice_detail_minimal.html").read_text(encoding="utf-8"),
        "1001",
    ).notice
    assert detail is not None
    wire = FakeWire(
        [
            response(200, list_html, **{"content-type": "text/html"}),
        ]
    )
    with collector(
        tmp_path / "db.sqlite3", wire, FakeSleeper(), FakeClock()
    ) as running:
        _ = running.store.save_detail(
            NoticeDetail(detail.summary, "metadata_body_token")
        )
        result = anyio.run(
            lambda: running.run(
                run_id="metadata-only",
                config=CollectorConfig(mode=CollectionMode.METADATA_ONLY),
            )
        )
        stored = running.store.get(detail.summary.duid)
        assert stored is not None
        assert stored.body is None
        assert running.store.search("metadata_body_token") == ()
    assert result.status is CollectStatus.SUCCESS
    assert wire.calls == [
        PAGE_URL,
    ]


@pytest.mark.parametrize(
    ("status", "body", "expected_reason"),
    [
        (403, b"denied", "forbidden"),
        (429, b"slow down", "rate_limit"),
        (500, b"server error", "http_failure"),
        (200, b"<html><body>CAPTCHA challenge</body></html>", "captcha"),
        (200, b"<html><body>WAF challenge</body></html>", "captcha"),
        (200, b"<html><title>not a board</title></html>", "markup"),
        (302, b"", "cross_host_redirect"),
    ],
    ids=[
        "forbidden",
        "rate_limit",
        "server_error",
        "captcha",
        "waf",
        "markup",
        "redirect",
    ],
)
def test_metadata_only_page_failures_block_after_one_page_request_without_mutation(
    tmp_path: Path, status: int, body: bytes, expected_reason: str
) -> None:
    """Given a blocked page-one response, preserve notices, FTS, and revisions."""
    detail = parse_detail_html(
        (FIXTURES / "notice_detail_minimal.html").read_text(encoding="utf-8"),
        "1001",
    ).notice
    assert detail is not None
    headers = {"content-type": "text/html"}
    if status == 302:
        headers["location"] = "https://evil.invalid/notice.jsp"
    wire = FakeWire([response(status, body, **headers)])
    with collector(
        tmp_path / "db.sqlite3", wire, FakeSleeper(), FakeClock()
    ) as running:
        _ = running.store.save_detail(NoticeDetail(detail.summary, "body_token"))
        before = (
            running.store.get(detail.summary.duid),
            running.store.revision_count(detail.summary.duid),
            running.store.search("body_token"),
        )
        result = anyio.run(
            lambda: running.run(
                run_id=f"metadata-failure-{expected_reason}",
                config=CollectorConfig(mode=CollectionMode.METADATA_ONLY),
            )
        )
        after = (
            running.store.get(detail.summary.duid),
            running.store.revision_count(detail.summary.duid),
            running.store.search("body_token"),
        )

    assert result.status is CollectStatus.BLOCKED
    assert result.reason == expected_reason
    assert wire.calls == [PAGE_URL]
    assert after == before


def test_metadata_only_page_is_atomic_when_row_two_write_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Given a row-two write failure, no metadata row or body projection changes."""
    list_html = (FIXTURES / "board_list_minimal.html").read_bytes()
    parsed = parse_list_html(list_html.decode())
    wire = FakeWire(
        [
            response(200, list_html, **{"content-type": "text/html"}),
        ]
    )
    with collector(
        tmp_path / "db.sqlite3", wire, FakeSleeper(), FakeClock()
    ) as running:
        for summary in parsed.records:
            _ = running.store.save_detail(
                NoticeDetail(summary, f"body_token_{summary.duid}")
            )
        before = tuple(
            (
                running.store.get(summary.duid),
                running.store.revision_count(summary.duid),
                running.store.search(f"body_token_{summary.duid}"),
            )
            for summary in parsed.records
        )
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
                run_id="metadata-atomic",
                config=CollectorConfig(mode=CollectionMode.METADATA_ONLY),
            )
        )
        after = tuple(
            (
                running.store.get(summary.duid),
                running.store.revision_count(summary.duid),
                running.store.search(f"body_token_{summary.duid}"),
            )
            for summary in parsed.records
        )
        crawl = running.store.get_crawl("metadata-atomic")

    assert result.status is CollectStatus.BLOCKED
    assert result.reason == "collector_failure"
    assert after == before
    assert crawl is not None
    assert crawl.status is CrawlStatus.FAILED
    assert crawl.finished_at is not None
    assert crawl.pages_seen == 1
    assert crawl.detail_requests == 0
    assert crawl.index_requests == 1
    assert crawl.retry_count == 0
