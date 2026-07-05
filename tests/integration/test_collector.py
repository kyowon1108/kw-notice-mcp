"""Integration tests for collector state and SQLite crawl leases."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import anyio

from kw_notice_mcp.collector import Collector, CollectStatus
from kw_notice_mcp.storage import open_storage
from kw_notice_mcp.storage_models import CrawlStatus
from kw_notice_mcp.wire import WireResponse

FIXTURES = Path(__file__).parents[1] / "fixtures"
ROBOTS = b"User-agent: *\nAllow: /ko/life/notice.jsp\n"


class QueueWire:
    """A deterministic integration transport with bounded fixture responses."""

    responses: list[WireResponse]

    def __init__(self, responses: list[WireResponse]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    async def request(self, url: str) -> WireResponse:
        self.calls.append(url)
        return self.responses.pop(0)


async def _no_sleep(seconds: float) -> None:
    """Avoid wall-clock delay while retaining the production sleeper seam."""
    del seconds


class EmptyWire:
    """A transport that records whether a busy run touched the wire."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def request(self, url: str) -> WireResponse:
        self.calls.append(url)
        return WireResponse(200, {"content-type": "text/plain"}, b"User-agent: *")


def test_concurrent_run_is_busy_before_any_second_http_sequence(tmp_path: Path) -> None:
    """Given an active lease, a second collector returns busy without wire calls."""
    database = tmp_path / "db.sqlite3"
    now = datetime(2026, 8, 5, tzinfo=UTC)
    with open_storage(database) as store:
        _ = store.start_crawl("already-running", started_at=now)
        wire = EmptyWire()
        collector = Collector(
            store=store,
            transport=wire,
            clock=lambda: now,
        )

        result = anyio.run(lambda: collector.run(run_id="second"))

        assert result.status is CollectStatus.BUSY
        assert wire.calls == []
        active = store.get_crawl("already-running")
        assert active is not None
        assert active.status is CrawlStatus.RUNNING


def test_stale_run_is_interrupted_and_checkpoint_is_available(tmp_path: Path) -> None:
    """Given a run older than fifteen minutes, lease acquisition recovers it."""
    database = tmp_path / "db.sqlite3"
    now = datetime(2026, 8, 5, tzinfo=UTC)
    with open_storage(database) as store:
        _ = store.start_crawl("old", started_at=now - timedelta(minutes=16))
        _ = store.update_crawl(
            "old", checkpoint_page=3, updated_at=now - timedelta(minutes=16)
        )
        wire = EmptyWire()
        collector = Collector(store=store, transport=wire, clock=lambda: now)

        result = anyio.run(lambda: collector.run(run_id="recovery"))

        assert result.status is CollectStatus.BLOCKED
        recovered = store.get_crawl("old")
        assert recovered is not None
        assert recovered.status is CrawlStatus.INTERRUPTED
        assert store.restart_checkpoint() == 3


def test_valid_robots_commits_new_detail_then_skips_unchanged_detail(
    tmp_path: Path,
) -> None:
    """Given valid robots, a new detail commits once and unchanged metadata skips."""
    database = tmp_path / "db.sqlite3"
    list_html = """
    <section class="board-list-box">
      <a class="title" href="/ko/life/notice.jsp?DUID=1001">Detail sample notice</a>
      <span class="category">학사</span>
      <span class="posted-date">2026.08.01</span>
      <span class="updated-date">2026.08.02</span>
      <span class="department">Academic Office</span>
    </section>
    """.encode()
    detail_html = (FIXTURES / "notice_detail_minimal.html").read_bytes()
    first = QueueWire(
        [
            WireResponse(200, {"content-type": "text/plain"}, ROBOTS),
            WireResponse(200, {"content-type": "text/html"}, list_html),
            WireResponse(200, {"content-type": "text/html"}, detail_html),
        ]
    )
    second = QueueWire(
        [
            WireResponse(200, {"content-type": "text/plain"}, ROBOTS),
            WireResponse(200, {"content-type": "text/html"}, list_html),
        ]
    )

    with open_storage(database) as store:
        first_result = anyio.run(
            lambda: Collector(store=store, transport=first, sleeper=_no_sleep).run(
                run_id="valid-first"
            )
        )
        second_result = anyio.run(
            lambda: Collector(store=store, transport=second, sleeper=_no_sleep).run(
                run_id="valid-second"
            )
        )

        assert first_result.status is CollectStatus.SUCCESS
        assert second_result.status is CollectStatus.SUCCESS
        assert len(store.search()) == 1
        assert len(first.calls) == 3
        assert len(second.calls) == 2
        assert all("sample.bin" not in call for call in (*first.calls, *second.calls))


def test_repeated_stale_interruptions_recover_without_overlap(tmp_path: Path) -> None:
    """Given repeated stale leases, each is interrupted before a new HTTP sequence."""
    database = tmp_path / "db.sqlite3"
    now = datetime(2026, 8, 5, tzinfo=UTC)
    with open_storage(database) as store:
        for number in (1, 2):
            stale_id = f"stale-{number}"
            _ = store.start_crawl(stale_id, started_at=now - timedelta(minutes=16))
            wire = EmptyWire()
            result = anyio.run(
                lambda wire=wire, number=number: Collector(
                    store=store,
                    transport=wire,
                    sleeper=_no_sleep,
                    clock=lambda: now,
                ).run(run_id=f"recovery-{number}")
            )
            recovered = store.get_crawl(stale_id)
            assert result.status is CollectStatus.BLOCKED
            assert recovered is not None
            assert recovered.status is CrawlStatus.INTERRUPTED


def test_failed_detail_does_not_checkpoint_incomplete_page(tmp_path: Path) -> None:
    """Given a blocked detail, leave the current page eligible for recovery."""
    list_html = """
    <section class="board-list-box">
      <a class="title" href="/ko/life/notice.jsp?DUID=1001">Detail sample notice</a>
      <span class="category">학사</span><span class="posted-date">2026.08.01</span>
      <span class="updated-date">2026.08.02</span>
      <span class="department">Academic Office</span>
    </section>
    """.encode()
    wire = QueueWire(
        [
            WireResponse(200, {"content-type": "text/plain"}, ROBOTS),
            WireResponse(200, {"content-type": "text/html"}, list_html),
            WireResponse(403, {"content-type": "text/html"}, b"denied"),
        ]
    )
    with open_storage(tmp_path / "db.sqlite3") as store:
        result = anyio.run(
            lambda: Collector(store=store, transport=wire, sleeper=_no_sleep).run(
                run_id="incomplete-page"
            )
        )
        crawl = store.get_crawl("incomplete-page")
        assert store.search() == ()
    assert result.status is CollectStatus.BLOCKED
    assert crawl is not None
    assert crawl.checkpoint_page == 0
