"""Metadata-only collector path used by the operational CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kw_notice_mcp.collector_models import CollectStatus
from kw_notice_mcp.collector_policy import time_exhausted
from kw_notice_mcp.collector_wire import content_type, decode_html, request_with_policy
from kw_notice_mcp.parser import parse_list_html
from kw_notice_mcp.source import build_list_url
from kw_notice_mcp.wire import WireRole

HTTP_OK = 200

if TYPE_CHECKING:
    from datetime import datetime

    from kw_notice_mcp.collector_context import CollectorContext
    from kw_notice_mcp.collector_models import CollectionResult, CollectorConfig
    from kw_notice_mcp.robots import RobotsResult


async def run_metadata_collection(
    context: CollectorContext,
    *,
    run_id: str,
    config: CollectorConfig,
    policy: RobotsResult,
    started: datetime,
) -> CollectionResult:
    """Request exactly page one and persist metadata without body retention."""
    if time_exhausted(context.clock, started, config):
        return context.finish(run_id, CollectStatus.BUDGET_EXCEEDED, "time_budget")
    index = await request_with_policy(
        context.transport,
        context.budget,
        context.sleeper,
        str(build_list_url(1)),
        WireRole.NOTICE,
        policy.crawl_delay,
        context.counters,
    )
    if index.reason is not None or index.response is None:
        return context.finish(run_id, CollectStatus.BLOCKED, index.reason)
    if (
        index.response.status_code != HTTP_OK
        or content_type(index.response) != "text/html"
    ):
        return context.finish(run_id, CollectStatus.BLOCKED, "markup")
    html = decode_html(index.response.body)
    if html is None:
        return context.finish(run_id, CollectStatus.BLOCKED, "markup")
    parsed = parse_list_html(html)
    if parsed.issues:
        return context.finish(run_id, CollectStatus.BLOCKED, "markup")
    context.counters.record_page()
    _ = context.store.save_metadata_batch(parsed.records, collected_at=context.clock())
    _ = context.store.update_crawl(
        run_id,
        checkpoint_page=1,
        pages_seen=1,
        detail_requests=0,
        index_requests=context.counters.wire_requests,
        retry_count=context.counters.retries,
        updated_at=context.clock(),
    )
    return context.finish(run_id, CollectStatus.SUCCESS, None)
