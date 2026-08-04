"""Fail-closed, bounded, resumable KW notice collection."""

import uuid
from collections.abc import Callable
from datetime import datetime

from kw_notice_mcp.collector_context import CollectorCounters
from kw_notice_mcp.collector_models import (
    CollectionMode,
    CollectionResult,
    CollectorConfig,
    CollectStatus,
)
from kw_notice_mcp.collector_run import run_collection
from kw_notice_mcp.storage import CrawlBusyError, SQLiteNoticeStore
from kw_notice_mcp.storage_models import CrawlStatus
from kw_notice_mcp.storage_support import utc_now
from kw_notice_mcp.wire import (
    Sleeper,
    WireBudget,
    WireTransport,
    anyio_sleeper,
)

__all__ = [
    "CollectStatus",
    "CollectionMode",
    "CollectionResult",
    "Collector",
    "CollectorConfig",
]

Clock = Callable[[], datetime]


class Collector:
    """One low-concurrency collector run against an injected wire boundary."""

    def __init__(
        self,
        *,
        store: SQLiteNoticeStore,
        transport: WireTransport,
        sleeper: Sleeper | None = None,
        clock: Clock = utc_now,
        budget: WireBudget | None = None,
    ) -> None:
        """Create a collector whose network, delay, and time are injectable."""
        self.store: SQLiteNoticeStore = store
        self.transport: WireTransport = transport
        self.sleeper: Sleeper = sleeper or anyio_sleeper
        self.clock: Clock = clock
        self.budget: WireBudget = budget or WireBudget()
        self.counters: CollectorCounters = CollectorCounters()

    def finish(
        self,
        run_id: str,
        status: CollectStatus,
        reason: str | None,
        *,
        wire_requests: int | None = None,
        retries: int | None = None,
    ) -> CollectionResult:
        """Persist only crawl-run lifecycle state and produce a safe result."""
        actual_wire_requests = (
            self.counters.wire_requests if wire_requests is None else wire_requests
        )
        actual_retries = self.counters.retries if retries is None else retries
        current = self.store.get_crawl(run_id)
        _ = self.store.update_crawl(
            run_id,
            pages_seen=self.counters.pages_seen if current is not None else 0,
            detail_requests=self.counters.detail_requests if current is not None else 0,
            index_requests=actual_wire_requests,
            retry_count=actual_retries,
            block_reason=reason,
            updated_at=self.clock(),
        )
        final = (
            CrawlStatus.SUCCESS
            if status is CollectStatus.SUCCESS
            else CrawlStatus.BLOCKED
        )
        _ = self.store.finish_crawl(run_id, status=final, finished_at=self.clock())
        return CollectionResult(
            status, run_id, reason, actual_wire_requests, actual_retries
        )

    async def run(
        self,
        *,
        run_id: str | None = None,
        config: CollectorConfig | None = None,
    ) -> CollectionResult:
        """Run robots-first collection and commit notices only after full success."""
        actual_run_id = run_id or uuid.uuid4().hex
        actual_config = config or CollectorConfig()
        self.counters = CollectorCounters()
        try:
            _ = self.store.start_crawl(actual_run_id, started_at=self.clock())
        except CrawlBusyError:
            return CollectionResult(CollectStatus.BUSY, actual_run_id, "busy", 0, 0)
        return await run_collection(self, run_id=actual_run_id, config=actual_config)
