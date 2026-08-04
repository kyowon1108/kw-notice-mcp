"""FULL collector failure accounting regression tests."""

from dataclasses import dataclass
from pathlib import Path

import anyio
import httpx2
import pytest

from kw_notice_mcp.collector import CollectorConfig, CollectStatus
from kw_notice_mcp.storage_models import CrawlStatus
from kw_notice_mcp.wire import ResponseTooLargeError, WireBudget

from .collector_full_support import detail_body, full_wire
from .collector_test_support import FakeClock, FakeSleeper, collector


@dataclass(frozen=True, slots=True)
class _FailureCase:
    """One post-index failure and its expected persisted accounting."""

    failure: BaseException
    reason: str
    requests: int
    details: int
    status: CrawlStatus


@pytest.mark.parametrize(
    "case",
    [
        _FailureCase(
            httpx2.ReadError("read"), "transport_failure", 6, 2, CrawlStatus.BLOCKED
        ),
        _FailureCase(ResponseTooLargeError(), "oversized", 4, 2, CrawlStatus.BLOCKED),
        _FailureCase(
            RuntimeError("unexpected"), "collector_failure", 4, 2, CrawlStatus.FAILED
        ),
    ],
    ids=[
        "read-error-after-requests",
        "oversize-after-requests",
        "unexpected-after-requests",
    ],
)
def test_full_failures_retain_actual_wire_and_detail_counters(
    tmp_path: Path,
    case: _FailureCase,
) -> None:
    """Given a post-index failure, finalize with actual attempts and no lease."""
    wire = full_wire(detail_body(), case.failure, case.failure, case.failure)
    with collector(
        tmp_path / "db.sqlite3", wire, FakeSleeper(), FakeClock()
    ) as running:
        result = anyio.run(
            lambda: running.run(
                run_id="full-counter-failure",
                config=CollectorConfig(max_detail_requests=2),
            )
        )
        crawl = running.store.get_crawl("full-counter-failure")

    assert result.reason == case.reason
    assert result.wire_requests == case.requests
    assert result.retry_count == (2 if case.reason == "transport_failure" else 0)
    assert crawl is not None
    assert crawl.status is case.status
    assert crawl.finished_at is not None
    assert crawl.pages_seen == 1
    assert crawl.detail_requests == case.details
    assert crawl.index_requests == case.requests


def test_full_budget_after_index_retains_completed_request_counters(
    tmp_path: Path,
) -> None:
    """Given a budget exhausted after the index, retain completed page counters."""
    wire = full_wire(detail_body())
    with collector(
        tmp_path / "db.sqlite3",
        wire,
        FakeSleeper(),
        FakeClock(),
        budget=WireBudget(maximum=2),
    ) as running:
        result = anyio.run(
            lambda: running.run(
                run_id="full-budget-after-index",
                config=CollectorConfig(max_detail_requests=1),
            )
        )
        crawl = running.store.get_crawl("full-budget-after-index")

    assert result.status is CollectStatus.BUDGET_EXCEEDED
    assert result.reason == "wire_budget"
    assert result.wire_requests == 2
    assert crawl is not None
    assert crawl.pages_seen == 1
    assert crawl.detail_requests == 0
    assert crawl.index_requests == 2
    assert crawl.finished_at is not None
