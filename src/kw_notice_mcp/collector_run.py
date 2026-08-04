"""Run orchestration kept separate from the collector's wire policy."""

import sqlite3

from kw_notice_mcp.collector_context import CollectorContext
from kw_notice_mcp.collector_crawl import crawl_notice_pages
from kw_notice_mcp.collector_metadata import run_metadata_collection
from kw_notice_mcp.collector_models import (
    CollectionMode,
    CollectionResult,
    CollectorConfig,
    CollectStatus,
)
from kw_notice_mcp.collector_policy import (
    blocked_policy_reason,
    robots_missing_requires_full,
)
from kw_notice_mcp.collector_wire import content_type, request_with_policy
from kw_notice_mcp.robots import parse_robots_response
from kw_notice_mcp.storage_errors import StorageError
from kw_notice_mcp.storage_models import CrawlStatus
from kw_notice_mcp.wire import ResponseTooLargeError, WireBudgetExceededError, WireRole

HTTP_OK = 200


def _finish_blocked(
    context: CollectorContext,
    run_id: str,
    reason: str,
) -> CollectionResult:
    """Finish a policy-blocked run without mutating notice data."""
    return context.finish(run_id, CollectStatus.BLOCKED, reason)


def _finish_failed(
    context: CollectorContext,
    run_id: str,
) -> CollectionResult:
    """Close an unexpected collector failure without leaving a running lease."""
    current = context.store.get_crawl(run_id)
    wire_requests = context.counters.wire_requests
    retries = context.counters.retries
    _ = context.store.update_crawl(
        run_id,
        pages_seen=context.counters.pages_seen if current is not None else 0,
        detail_requests=context.counters.detail_requests if current is not None else 0,
        index_requests=wire_requests,
        retry_count=retries,
        block_reason="collector_failure",
        updated_at=context.clock(),
    )
    _ = context.store.finish_crawl(
        run_id, status=CrawlStatus.FAILED, finished_at=context.clock()
    )
    return CollectionResult(
        CollectStatus.BLOCKED, run_id, "collector_failure", wire_requests, retries
    )


async def _run_collection(
    context: CollectorContext,
    *,
    run_id: str,
    config: CollectorConfig,
) -> CollectionResult:
    """Run the policy and collection paths after failure accounting is set up."""
    started = context.clock()
    robots = await request_with_policy(
        context.transport,
        context.budget,
        context.sleeper,
        "https://www.kw.ac.kr/robots.txt",
        WireRole.ROBOTS,
        2.0,
        context.counters,
    )
    if robots.reason is not None or robots.response is None:
        return context.finish(run_id, CollectStatus.BLOCKED, robots.reason)
    if robots.response.status_code != HTTP_OK:
        return _finish_blocked(context, run_id, "robots_http_failure")
    policy = parse_robots_response(
        robots.response.status_code, content_type(robots.response), robots.response.body
    )
    if robots_missing_requires_full(policy, config.mode):
        return _finish_blocked(context, run_id, "robots_missing_metadata_only_required")
    if policy.mode is None:
        return _finish_blocked(context, run_id, blocked_policy_reason(policy))
    if config.mode is CollectionMode.METADATA_ONLY:
        return await run_metadata_collection(
            context,
            run_id=run_id,
            config=config,
            policy=policy,
            started=started,
        )
    return await crawl_notice_pages(
        context,
        run_id=run_id,
        config=config,
        policy=policy,
        started=started,
    )


async def run_collection(
    context: CollectorContext,
    *,
    run_id: str,
    config: CollectorConfig,
) -> CollectionResult:
    """Fetch robots, stage changed details, then commit only on full success."""
    try:
        return await _run_collection(
            context,
            run_id=run_id,
            config=config,
        )
    except WireBudgetExceededError:
        return context.finish(run_id, CollectStatus.BUDGET_EXCEEDED, "wire_budget")
    except ResponseTooLargeError:
        return context.finish(run_id, CollectStatus.BLOCKED, "oversized")
    except (OSError, RuntimeError, sqlite3.Error, StorageError):
        return _finish_failed(context, run_id)
