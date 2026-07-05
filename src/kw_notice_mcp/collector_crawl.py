"""Standard notice-page crawling and atomic publication."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from kw_notice_mcp.collector_models import (
    MAX_DETAILS,
    CollectionResult,
    CollectorConfig,
    CollectStatus,
)
from kw_notice_mcp.collector_policy import needs_detail, time_exhausted
from kw_notice_mcp.collector_wire import content_type, decode_html, request_with_policy
from kw_notice_mcp.parser import parse_detail_html, parse_list_html
from kw_notice_mcp.source import build_detail_url, build_list_url
from kw_notice_mcp.wire import WireRole

if TYPE_CHECKING:
    from datetime import datetime

    from kw_notice_mcp.collector_context import CollectorContext
    from kw_notice_mcp.domain import NoticeDetail, NoticeSummary
    from kw_notice_mcp.robots import RobotsResult
    from kw_notice_mcp.values import DUID

HTTP_OK = 200
HTTP_NOT_FOUND = 404


@dataclass(frozen=True, slots=True)
class _IndexFetch:
    """One parsed index response and its bounded wire accounting."""

    records: tuple[NoticeSummary, ...]
    requests: int
    retries: int
    reason: str | None


@dataclass(frozen=True, slots=True)
class _DetailFetch:
    """One parsed detail response and its bounded wire accounting."""

    notice: NoticeDetail | None
    tombstone: bool
    requests: int
    retries: int
    reason: str | None


async def _fetch_index(
    context: CollectorContext, page: int, policy: RobotsResult
) -> _IndexFetch:
    """Fetch and parse one notice index page."""
    result = await request_with_policy(
        context.transport,
        context.budget,
        context.sleeper,
        str(build_list_url(page)),
        WireRole.NOTICE,
        policy.crawl_delay,
        context.counters,
    )
    if result.reason is not None or result.response is None:
        return _IndexFetch((), result.requests, result.retries, result.reason)
    response = result.response
    if response.status_code != HTTP_OK or content_type(response) != "text/html":
        return _IndexFetch((), result.requests, result.retries, "markup")
    html = decode_html(response.body)
    if html is None:
        return _IndexFetch((), result.requests, result.retries, "markup")
    parsed = parse_list_html(html)
    if parsed.issues:
        return _IndexFetch((), result.requests, result.retries, "markup")
    return _IndexFetch(parsed.records, result.requests, result.retries, None)


async def _fetch_detail(
    context: CollectorContext,
    summary: NoticeSummary,
    policy: RobotsResult,
) -> _DetailFetch:
    """Fetch and parse one changed notice detail page."""
    result = await request_with_policy(
        context.transport,
        context.budget,
        context.sleeper,
        str(build_detail_url(summary.duid)),
        WireRole.NOTICE,
        policy.crawl_delay,
        context.counters,
        detail=True,
    )
    if result.reason is not None:
        return _DetailFetch(
            notice=None,
            tombstone=False,
            requests=result.requests,
            retries=result.retries,
            reason=result.reason,
        )
    if result.response is None:
        return _DetailFetch(
            notice=None,
            tombstone=False,
            requests=result.requests,
            retries=result.retries,
            reason="detail_failure",
        )
    response = result.response
    if response.status_code == HTTP_NOT_FOUND:
        return _DetailFetch(
            notice=None,
            tombstone=True,
            requests=result.requests,
            retries=result.retries,
            reason=None,
        )
    if content_type(response) != "text/html":
        return _DetailFetch(
            notice=None,
            tombstone=False,
            requests=result.requests,
            retries=result.retries,
            reason="markup",
        )
    html = decode_html(response.body)
    if html is not None:
        detail = parse_detail_html(html, str(summary.duid))
        if not detail.issues and detail.notice is not None:
            return _DetailFetch(
                notice=detail.notice,
                tombstone=False,
                requests=result.requests,
                retries=result.retries,
                reason=None,
            )
    return _DetailFetch(
        notice=None,
        tombstone=False,
        requests=result.requests,
        retries=result.retries,
        reason="markup",
    )


async def crawl_notice_pages(  # noqa: C901
    context: CollectorContext,
    *,
    run_id: str,
    config: CollectorConfig,
    policy: RobotsResult,
    started: datetime,
) -> CollectionResult:
    """Fetch, stage, and publish standard notice pages."""
    staged: list[NoticeDetail] = []
    tombstones: list[DUID] = []
    checkpoint = context.store.restart_checkpoint()
    first_page = max(1, (checkpoint or 0) + 1)
    page_count = min(max(config.max_pages, 1), 50)
    for page in range(first_page, first_page + page_count):
        if time_exhausted(context.clock, started, config):
            return context.finish(
                run_id,
                CollectStatus.BUDGET_EXCEEDED,
                "time_budget",
            )
        index = await _fetch_index(context, page, policy)
        if index.reason is not None:
            return context.finish(run_id, CollectStatus.BLOCKED, index.reason)
        context.counters.record_page()
        for summary in index.records:
            if not needs_detail(summary, context.store.get(summary.duid)):
                continue
            if time_exhausted(context.clock, started, config):
                return context.finish(
                    run_id,
                    CollectStatus.BUDGET_EXCEEDED,
                    "time_budget",
                )
            if context.counters.detail_requests >= min(
                config.max_detail_requests, MAX_DETAILS
            ):
                return context.finish(
                    run_id,
                    CollectStatus.BUDGET_EXCEEDED,
                    "detail_budget",
                )
            detail = await _fetch_detail(context, summary, policy)
            _ = context.store.update_crawl(
                run_id,
                detail_requests=context.counters.detail_requests,
                index_requests=context.counters.wire_requests,
                retry_count=context.counters.retries,
                updated_at=context.clock(),
            )
            if detail.reason is not None:
                return context.finish(run_id, CollectStatus.BLOCKED, detail.reason)
            if detail.tombstone:
                tombstones.append(summary.duid)
            elif detail.notice is not None:
                staged.append(detail.notice)
        _ = context.store.update_crawl(
            run_id,
            checkpoint_page=page,
            pages_seen=context.counters.pages_seen,
            detail_requests=context.counters.detail_requests,
            index_requests=context.counters.wire_requests,
            retry_count=context.counters.retries,
            updated_at=context.clock(),
        )
    _ = context.store.save_detail_batch(
        staged, tombstones, collected_at=context.clock()
    )
    return context.finish(run_id, CollectStatus.SUCCESS, None)
