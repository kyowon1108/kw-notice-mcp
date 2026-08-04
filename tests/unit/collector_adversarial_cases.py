"""Adversarial collector cases re-exported by the mandated test module."""

from pathlib import Path

import anyio
import httpx2
import pytest

from kw_notice_mcp.collector import CollectStatus
from kw_notice_mcp.storage_models import CrawlStatus
from kw_notice_mcp.wire import (
    MAX_RESPONSE_BYTES,
    MAX_WIRE_REQUESTS,
    TargetIssue,
    TargetIssueCode,
    WireBudget,
    WireBudgetExceededError,
    WireResponse,
    WireRole,
    validate_wire_target,
)

from .collector_test_support import (
    ROBOTS,
    FakeClock,
    FakeSleeper,
    FakeWire,
    collector,
    response,
)


class InjectedTransportError(RuntimeError):
    """Test-only unexpected transport failure."""


@pytest.mark.parametrize(
    ("status", "reason"),
    [(403, "forbidden"), (429, "rate_limit")],
    ids=["forbidden", "rate_limit"],
)
def test_rate_limit_and_forbidden_stop_without_retry(
    tmp_path: Path, status: int, reason: str
) -> None:
    """Given a refusal status, send exactly one robots request."""
    wire = FakeWire([response(status, b"denied", **{"content-type": "text/plain"})])
    sleeper = FakeSleeper()
    with collector(tmp_path / "db.sqlite3", wire, sleeper, FakeClock()) as running:
        result = anyio.run(lambda: running.run(run_id=f"status-{status}"))
    assert result.status is CollectStatus.BLOCKED
    assert result.reason == reason
    assert len(wire.calls) == 1
    assert sleeper.delays == [2.0]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b"<html>CAPTCHA challenge</html>", "captcha"),
        (b"<html><title>misleading success</title></html>", "markup"),
    ],
    ids=["captcha", "malformed_markup"],
)
def test_captcha_and_malformed_markup_stop_without_retry(
    tmp_path: Path, body: bytes, expected: str
) -> None:
    """Given a challenge or bad 200 page, do not fetch details or write."""
    wire = FakeWire(
        [
            response(200, ROBOTS, **{"content-type": "text/plain"}),
            response(200, body, **{"content-type": "text/html"}),
        ]
    )
    with collector(
        tmp_path / "db.sqlite3", wire, FakeSleeper(), FakeClock()
    ) as running:
        result = anyio.run(lambda: running.run(run_id=f"bad-{expected}"))
        assert running.store.search() == ()
    assert result.status is CollectStatus.BLOCKED
    assert result.reason == expected
    assert len(wire.calls) == 2


def test_oversized_response_blocks_before_parsing(tmp_path: Path) -> None:
    """Given more than four MiB, block with no notice mutation."""
    wire = FakeWire(
        [
            response(
                200,
                b"x" * (MAX_RESPONSE_BYTES + 1),
                **{"content-type": "text/plain"},
            )
        ]
    )
    with collector(
        tmp_path / "db.sqlite3", wire, FakeSleeper(), FakeClock()
    ) as running:
        result = anyio.run(lambda: running.run(run_id="oversized"))
        assert running.store.search() == ()
    assert result.status is CollectStatus.BLOCKED
    assert result.reason == "oversized"
    assert len(wire.calls) == 1


def test_same_host_redirect_hop_and_crawl_delay_apply_before_every_request(
    tmp_path: Path,
) -> None:
    """Given a safe redirect, delay every hop before sending."""
    delayed = b"User-agent: *\nCrawl-delay: 3.5\nAllow: /ko/life/notice.jsp\n"
    wire = FakeWire(
        [
            WireResponse(302, {"location": "/robots.txt?hop=1"}, b""),
            response(200, delayed, **{"content-type": "text/plain"}),
            response(
                200,
                b'<section class="board-list-box"></section>',
                **{"content-type": "text/html"},
            ),
        ]
    )
    sleeper = FakeSleeper()
    with collector(tmp_path / "db.sqlite3", wire, sleeper, FakeClock()) as running:
        result = anyio.run(lambda: running.run(run_id="same-host-hop"))
    assert result.status is CollectStatus.SUCCESS
    assert wire.calls[1] == "https://www.kw.ac.kr/robots.txt?hop=1"
    assert sleeper.delays == [2.0, 2.0, 3.5]


def test_redirect_hop_cap_blocks_after_five_follows(tmp_path: Path) -> None:
    """Given a redirect loop, issue six requests and never a seventh."""
    wire = FakeWire(
        [WireResponse(302, {"location": "/robots.txt"}, b"") for _ in range(6)]
    )
    sleeper = FakeSleeper()
    with collector(tmp_path / "db.sqlite3", wire, sleeper, FakeClock()) as running:
        result = anyio.run(lambda: running.run(run_id="redirect-hop-cap"))
    assert result.status is CollectStatus.BLOCKED
    assert result.reason == "redirect_hop_cap"
    assert len(wire.calls) == 6
    assert sleeper.delays == [2.0] * 6


def test_hung_timeout_is_three_total_attempts(tmp_path: Path) -> None:
    """Given repeated timeouts, block after three delayed attempts."""
    wire = FakeWire([TimeoutError(), TimeoutError(), TimeoutError()])
    sleeper = FakeSleeper()
    with collector(tmp_path / "db.sqlite3", wire, sleeper, FakeClock()) as running:
        result = anyio.run(lambda: running.run(run_id="hung-timeout"))
    assert result.status is CollectStatus.BLOCKED
    assert result.reason == "transport_failure"
    assert len(wire.calls) == 3
    assert sleeper.delays == [2.0] * 3


@pytest.mark.parametrize(
    "error",
    [
        httpx2.ReadError("read"),
        httpx2.WriteError("write"),
        httpx2.CloseError("close"),
        httpx2.StreamError("stream"),
    ],
    ids=["read", "write", "close", "stream"],
)
def test_httpx_request_errors_are_typed_retryable_outcomes(
    tmp_path: Path, error: httpx2.RequestError | httpx2.StreamError
) -> None:
    """Given an httpx2 request error, return a bounded transport outcome."""
    wire = FakeWire([error, error, error])
    sleeper = FakeSleeper()
    with collector(tmp_path / "db.sqlite3", wire, sleeper, FakeClock()) as running:
        result = anyio.run(lambda: running.run(run_id="httpx-request-error"))

    assert result.status is CollectStatus.BLOCKED
    assert result.reason == "transport_failure"
    assert len(wire.calls) == 3


def test_unexpected_transport_failure_closes_the_crawl_lease(tmp_path: Path) -> None:
    """Given an unexpected transport failure, finish the run as failed."""
    wire = FakeWire([InjectedTransportError()])
    with collector(
        tmp_path / "db.sqlite3", wire, FakeSleeper(), FakeClock()
    ) as running:
        result = anyio.run(lambda: running.run(run_id="unexpected-transport"))
        crawl = running.store.get_crawl("unexpected-transport")

    assert result.status is CollectStatus.BLOCKED
    assert result.reason == "collector_failure"
    assert crawl is not None
    assert crawl.status is CrawlStatus.FAILED
    assert crawl.finished_at is not None


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("http://www.kw.ac.kr/robots.txt", TargetIssueCode.SCHEME),
        (
            "https://user" + chr(64) + "www.kw.ac.kr/robots.txt",
            TargetIssueCode.USERINFO,
        ),
        ("https://www.kw.ac.kr:444/robots.txt", TargetIssueCode.PORT),
        ("https://www.kw.ac.kr/robots.txt#fragment", TargetIssueCode.FRAGMENT),
    ],
    ids=["invalid_scheme", "userinfo", "invalid_port", "fragment"],
)
def test_invalid_scheme_userinfo_port_and_fragment_are_rejected_before_wire(
    url: str, code: TargetIssueCode
) -> None:
    """Given an invalid target, return a typed preflight rejection."""
    result = validate_wire_target(url, WireRole.ROBOTS)
    assert isinstance(result, TargetIssue)
    assert result.code is code


def test_global_wire_budget_is_exactly_five_hundred() -> None:
    """Given the default budget, reject request 501 before consumption."""
    budget = WireBudget()
    for _ in range(MAX_WIRE_REQUESTS):
        budget.consume()
    with pytest.raises(WireBudgetExceededError):
        budget.consume()
    assert budget.used == 500


def test_logs_exclude_response_bodies_and_pii(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Given sensitive response text, retain neither body nor identifier."""
    sentinel = "private.person" + "@" + "example.invalid"
    wire = FakeWire(
        [
            response(
                200,
                f"<html>{sentinel}</html>".encode(),
                **{"content-type": "text/html"},
            )
        ]
    )
    with collector(
        tmp_path / "db.sqlite3", wire, FakeSleeper(), FakeClock()
    ) as running:
        result = anyio.run(lambda: running.run(run_id="safe-logs"))
        crawl = running.store.get_crawl("safe-logs")
    assert result.status is CollectStatus.BLOCKED
    assert crawl is not None
    assert sentinel not in caplog.text
    assert "<html>" not in caplog.text
    assert sentinel not in (crawl.block_reason or "")
