"""Unit tests for deterministic wire and collector behavior."""

from pathlib import Path
from types import TracebackType
from typing import Never

import anyio
import httpx2
import pytest

from kw_notice_mcp.collector import CollectorConfig, CollectStatus
from kw_notice_mcp.wire import (
    HttpxWireTransport,
    ResponseTooLargeError,
    WireBudget,
    WireConnectionError,
    WireResponse,
)

from . import collector_adversarial_cases as cases
from .collector_test_support import (
    FIXTURES,
    ROBOTS,
    FakeClock,
    FakeSleeper,
    FakeWire,
    collector,
    response,
)

test_captcha_and_malformed_markup_stop_without_retry = (
    cases.test_captcha_and_malformed_markup_stop_without_retry
)
test_global_wire_budget_is_exactly_five_hundred = (
    cases.test_global_wire_budget_is_exactly_five_hundred
)
test_hung_timeout_is_three_total_attempts = (
    cases.test_hung_timeout_is_three_total_attempts
)
test_invalid_scheme_userinfo_port_and_fragment_are_rejected_before_wire = (
    cases.test_invalid_scheme_userinfo_port_and_fragment_are_rejected_before_wire
)
test_logs_exclude_response_bodies_and_pii = (
    cases.test_logs_exclude_response_bodies_and_pii
)
test_oversized_response_blocks_before_parsing = (
    cases.test_oversized_response_blocks_before_parsing
)
test_rate_limit_and_forbidden_stop_without_retry = (
    cases.test_rate_limit_and_forbidden_stop_without_retry
)
test_redirect_hop_cap_blocks_after_five_follows = (
    cases.test_redirect_hop_cap_blocks_after_five_follows
)
test_same_host_redirect_hop_and_crawl_delay_apply_before_every_request = (
    cases.test_same_host_redirect_hop_and_crawl_delay_apply_before_every_request
)


def test_valid_robots_orders_index_then_changed_detail_and_delays_each_request(
    tmp_path: Path,
) -> None:
    """Given valid fixtures, only new details are fetched after the index."""
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
    wire = FakeWire(
        [
            response(200, ROBOTS, **{"content-type": "text/plain"}),
            response(200, list_html, **{"content-type": "text/html"}),
            response(200, detail_html, **{"content-type": "text/html"}),
        ]
    )
    sleeper = FakeSleeper()
    with collector(tmp_path / "db.sqlite3", wire, sleeper, FakeClock()) as running:
        result = anyio.run(lambda: running.run(run_id="unit-valid"))
    assert result.status is CollectStatus.SUCCESS
    assert wire.calls == [
        "https://www.kw.ac.kr/robots.txt",
        "https://www.kw.ac.kr/ko/life/notice.jsp?srCategoryId=&mode=list&searchKey=1&searchVal=&tpage=1",
        "https://www.kw.ac.kr/ko/life/notice.jsp?BoardMode=view&DUID=1001",
    ]
    assert sleeper.delays == [2.0, 2.0, 2.0]
    assert all("sample.bin" not in call for call in wire.calls)


class FailingStream:
    """Async stream context that exposes one httpx2 read failure."""

    async def __aenter__(self) -> Never:
        """Raise the transport failure at stream acquisition."""
        message = "read"
        raise httpx2.ReadError(message)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Complete the context-manager contract after the failure."""
        del exc_type, exc, traceback


def test_httpx_adapter_translates_read_error_to_typed_wire_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given an httpx2 read error, the adapter exposes a typed wire error."""
    client = httpx2.AsyncClient()

    def fail_stream(method: str, url: str) -> FailingStream:
        del method, url
        return FailingStream()

    monkeypatch.setattr(client, "stream", fail_stream)
    transport = HttpxWireTransport(client)

    async def request_and_close() -> None:
        try:
            _ = await transport.request("https://www.kw.ac.kr/robots.txt")
        finally:
            await client.aclose()

    with pytest.raises(WireConnectionError):
        anyio.run(request_and_close)


def test_connection_retries_are_three_total_attempts_with_delay(tmp_path: Path) -> None:
    """Given connection failures, only the retryable boundary is retried."""
    wire = FakeWire(
        [
            WireConnectionError("connect"),
            WireConnectionError("connect"),
            response(200, ROBOTS, **{"content-type": "text/plain"}),
            response(
                200,
                b'<section class="board-list-box"></section>',
                **{"content-type": "text/html"},
            ),
        ]
    )
    sleeper = FakeSleeper()
    with collector(tmp_path / "db.sqlite3", wire, sleeper, FakeClock()) as running:
        result = anyio.run(
            lambda: running.run(run_id="retry", config=CollectorConfig(max_pages=1))
        )
    assert result.status is CollectStatus.SUCCESS
    assert wire.calls[:3] == ["https://www.kw.ac.kr/robots.txt"] * 3
    assert sleeper.delays[:3] == [2.0, 2.0, 2.0]


@pytest.mark.parametrize(
    ("location", "reason"),
    [
        ("https://evil.invalid/robots.txt", "cross_host_redirect"),
        ("https://www.kw.ac.kr/private", "disallowed_redirect_path"),
        ("https://www.kw.ac.kr:444/robots.txt", "invalid_redirect_target"),
        ("http://www.kw.ac.kr/robots.txt", "invalid_redirect_target"),
        (
            "https://user:pass" + chr(64) + "www.kw.ac.kr/robots.txt",
            "invalid_redirect_target",
        ),
    ],
)
def test_intermediate_cross_host_redirect_and_same_host_disallowed_path_redirect(
    tmp_path: Path, location: str, reason: str
) -> None:
    """Given an unsafe Location, block before the next wire call."""
    wire = FakeWire(
        [
            WireResponse(
                status_code=302,
                headers={"location": location, "content-type": "text/plain"},
                body=b"",
            )
        ]
    )
    with collector(
        tmp_path / "db.sqlite3", wire, FakeSleeper(), FakeClock()
    ) as running:
        result = anyio.run(lambda: running.run(run_id="redirect"))
    assert result.status is CollectStatus.BLOCKED
    assert result.reason == reason
    assert wire.calls == ["https://www.kw.ac.kr/robots.txt"]


def test_html_404_robots_blocks_without_notice_mutation(tmp_path: Path) -> None:
    """Given arbitrary HTML robots, only the FULL crawl run may change."""
    wire = FakeWire(
        [response(200, b"<html>custom 404</html>", **{"content-type": "text/html"})]
    )
    with collector(
        tmp_path / "db.sqlite3", wire, FakeSleeper(), FakeClock()
    ) as running:
        result = anyio.run(lambda: running.run(run_id="blocked"))
        assert running.store.table_names()
        assert running.store.search() == ()
    assert result.status is CollectStatus.BLOCKED
    assert result.reason == "robots_invalid_content_type"
    assert wire.calls == ["https://www.kw.ac.kr/robots.txt"]


def test_wire_budget_stops_before_the_next_request(tmp_path: Path) -> None:
    """Given a one-request test budget, the second call is impossible."""
    wire = FakeWire(
        [
            response(200, ROBOTS, **{"content-type": "text/plain"}),
            response(
                200,
                b'<section class="board-list-box"></section>',
                **{"content-type": "text/html"},
            ),
        ]
    )
    with collector(
        tmp_path / "db.sqlite3",
        wire,
        FakeSleeper(),
        FakeClock(),
        budget=WireBudget(maximum=1),
    ) as running:
        result = anyio.run(lambda: running.run(run_id="budget"))
    assert result.status is CollectStatus.BUDGET_EXCEEDED
    assert wire.calls == ["https://www.kw.ac.kr/robots.txt"]


def test_transport_oversized_error_stops_without_retry(tmp_path: Path) -> None:
    """Given a streaming overflow, never retry the oversized response."""
    wire = FakeWire([ResponseTooLargeError()])
    sleeper = FakeSleeper()
    with collector(tmp_path / "db.sqlite3", wire, sleeper, FakeClock()) as running:
        result = anyio.run(lambda: running.run(run_id="stream-oversized"))
    assert result.status is CollectStatus.BLOCKED
    assert result.reason == "oversized"
    assert len(wire.calls) == 1
    assert sleeper.delays == [2.0]


def test_time_budget_is_checked_before_detail_request(tmp_path: Path) -> None:
    """Given elapsed policy delays, stop before starting an over-budget detail."""
    list_html = """
    <section class="board-list-box">
      <a class="title" href="/ko/life/notice.jsp?DUID=1001">Detail sample notice</a>
      <span class="category">학사</span><span class="posted-date">2026.08.01</span>
      <span class="updated-date">2026.08.02</span>
      <span class="department">Academic Office</span>
    </section>
    """.encode()
    wire = FakeWire(
        [
            response(200, ROBOTS, **{"content-type": "text/plain"}),
            response(200, list_html, **{"content-type": "text/html"}),
        ]
    )
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    with collector(tmp_path / "db.sqlite3", wire, sleeper, clock) as running:
        result = anyio.run(
            lambda: running.run(
                run_id="time-budget",
                config=CollectorConfig(max_duration_seconds=3.0),
            )
        )
    assert result.status is CollectStatus.BUDGET_EXCEEDED
    assert result.reason == "time_budget"
    assert len(wire.calls) == 2
