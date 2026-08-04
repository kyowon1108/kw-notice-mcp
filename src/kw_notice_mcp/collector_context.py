"""Shared injected collector orchestration boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, final

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from kw_notice_mcp.collector_models import CollectionResult, CollectStatus
    from kw_notice_mcp.storage import SQLiteNoticeStore
    from kw_notice_mcp.wire import Sleeper, WireBudget, WireTransport


@final
class CollectorCounters:
    """Mutable per-run counters retained across normal and exceptional exits."""

    __slots__ = ("detail_requests", "pages_seen", "retries", "wire_requests")

    wire_requests: int
    retries: int
    pages_seen: int
    detail_requests: int

    def __init__(self) -> None:
        """Initialize an accumulator whose mutation is the documented purpose."""
        self.wire_requests = 0
        self.retries = 0
        self.pages_seen = 0
        self.detail_requests = 0

    def record_wire_request(self) -> None:
        """Record one request after the global budget reserves it."""
        self.wire_requests += 1

    def record_retry(self) -> None:
        """Record one retry before the next attempt begins."""
        self.retries += 1

    def record_page(self) -> None:
        """Record one successfully parsed index page."""
        self.pages_seen += 1

    def record_detail(self) -> None:
        """Record one detail wire request, including an exceptional response."""
        self.detail_requests += 1


class CollectorContext(Protocol):
    """The narrow mutable service boundary needed by collector paths."""

    store: SQLiteNoticeStore
    transport: WireTransport
    sleeper: Sleeper
    clock: Callable[[], datetime]
    budget: WireBudget
    counters: CollectorCounters

    def finish(
        self,
        run_id: str,
        status: CollectStatus,
        reason: str | None,
        *,
        wire_requests: int | None = None,
        retries: int | None = None,
    ) -> CollectionResult:
        """Persist crawl-only state and return a safe result."""
        ...
