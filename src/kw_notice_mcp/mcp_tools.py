"""Storage-only implementations of the four KW notice MCP tools."""

import sqlite3
from pathlib import Path

from kw_notice_mcp.domain import CATEGORY_CATALOG
from kw_notice_mcp.mcp_support import (
    DUID_PATTERN,
    MAX_QUERY_LENGTH,
    blocked,
    bounded_body,
    invalid,
    page,
    pagination,
    published_date,
    storage_error,
    summary,
)
from kw_notice_mcp.mcp_support import (
    category as parse_category,
)
from kw_notice_mcp.responses import (
    CategoryItem,
    CategoryResponse,
    ErrorEnvelope,
    LatestResponse,
    NoticeDetail,
    NoticeResponse,
    SearchResponse,
)
from kw_notice_mcp.storage import open_read_only_storage
from kw_notice_mcp.storage_errors import StorageError
from kw_notice_mcp.storage_models import CrawlStatus
from kw_notice_mcp.values import DUID


class NoticeToolService:
    """Read-only façade that opens SQLite in URI ``mode=ro`` per call."""

    def __init__(self, database: Path | str) -> None:
        """Store the database path without opening it or starting a crawl."""
        self.database: Path = Path(database)

    def search_notices(  # noqa: PLR0913
        self,
        query: str = "",
        category: str | None = None,
        published_from: str | None = None,
        published_to: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> SearchResponse:
        """Search cached notices using bounded, inclusive posted-date filters."""
        if type(query) is not str or len(query) > MAX_QUERY_LENGTH:
            return SearchResponse(
                items=[],
                query=query if type(query) is str else "",
                limit=limit,
                offset=offset,
                next_offset=None,
                has_more=False,
                error=invalid("query", "must be at most 200 characters"),
            )
        page_error = page(limit, offset)
        category_spec, category_error = parse_category(category)
        lower, lower_error = published_date(published_from, "published_from")
        upper, upper_error = published_date(published_to, "published_to")
        error = page_error or category_error or lower_error or upper_error
        if error is not None:
            return SearchResponse(
                items=[],
                query=query,
                limit=limit,
                offset=offset,
                next_offset=None,
                has_more=False,
                error=error,
            )
        if lower is not None and upper is not None and lower > upper:
            return SearchResponse(
                items=[],
                query=query,
                limit=limit,
                offset=offset,
                next_offset=None,
                has_more=False,
                error=invalid("published_from", "must not be after published_to"),
            )
        try:
            with open_read_only_storage(self.database) as store:
                category_id = str(category_spec.id) if category_spec else None
                rows = store.search(
                    query,
                    category_id=category_id,
                    limit=limit,
                    offset=offset,
                    posted_from=lower,
                    posted_to=upper,
                )
                total = store.search_count(
                    query, category_id=category_id, posted_from=lower, posted_to=upper
                )
                items = [summary(store, row) for row in rows]
                next_offset, has_more = pagination(len(items), limit, offset, total)
                is_blocked = store.latest_crawl_status() is CrawlStatus.BLOCKED
        except (StorageError, sqlite3.Error) as error:
            return SearchResponse(
                items=[],
                query=query,
                limit=limit,
                offset=offset,
                next_offset=None,
                has_more=False,
                error=storage_error(error),
            )
        return SearchResponse(
            items=items,
            query=query,
            limit=limit,
            offset=offset,
            next_offset=next_offset,
            has_more=has_more,
            error=blocked() if is_blocked else None,
        )

    def get_notice(self, duid: str) -> NoticeResponse:
        """Fetch one cached notice without fetching its source URL."""
        if type(duid) is not str or DUID_PATTERN.fullmatch(duid) is None:
            return NoticeResponse(
                notice=None, error=invalid("duid", "must contain 1 to 12 digits")
            )
        try:
            with open_read_only_storage(self.database) as store:
                stored = store.get(DUID(duid))
                is_blocked = store.latest_crawl_status() is CrawlStatus.BLOCKED
                if stored is None:
                    error: ErrorEnvelope = (
                        blocked()
                        if is_blocked
                        else ErrorEnvelope(
                            code="not_found",
                            message="Notice was not found.",
                            retryable=False,
                        )
                    )
                    return NoticeResponse(notice=None, error=error)
                if stored.tombstone_at is not None:
                    return NoticeResponse(
                        notice=None,
                        error=ErrorEnvelope(
                            code="tombstoned",
                            message="Notice is tombstoned.",
                            retryable=False,
                        ),
                    )
                result = NoticeDetail(
                    summary=summary(store, stored),
                    body=bounded_body(stored),
                )
        except (StorageError, sqlite3.Error) as storage_failure:
            return NoticeResponse(notice=None, error=storage_error(storage_failure))
        return NoticeResponse(notice=result, error=blocked() if is_blocked else None)

    def list_latest_notices(
        self, category: str | None = None, limit: int = 10, offset: int = 0
    ) -> LatestResponse:
        """List cached notices by posted date descending with stable DUID ties."""
        page_error = page(limit, offset)
        category_spec, category_error = parse_category(category)
        error = page_error or category_error
        if error is not None:
            return LatestResponse(
                items=[],
                limit=limit,
                offset=offset,
                next_offset=None,
                has_more=False,
                error=error,
            )
        try:
            with open_read_only_storage(self.database) as store:
                category_id = str(category_spec.id) if category_spec else None
                rows = store.latest(category_id=category_id, limit=limit, offset=offset)
                total = store.latest_count(category_id=category_id)
                items = [summary(store, row) for row in rows]
                next_offset, has_more = pagination(len(items), limit, offset, total)
                is_blocked = store.latest_crawl_status() is CrawlStatus.BLOCKED
        except (StorageError, sqlite3.Error) as error:
            return LatestResponse(
                items=[],
                limit=limit,
                offset=offset,
                next_offset=None,
                has_more=False,
                error=storage_error(error),
            )
        return LatestResponse(
            items=items,
            limit=limit,
            offset=offset,
            next_offset=next_offset,
            has_more=has_more,
            error=blocked() if is_blocked else None,
        )

    def list_categories(self) -> CategoryResponse:
        """Return all eleven categories in canonical order, including zero counts."""
        try:
            with open_read_only_storage(self.database) as store:
                counts = dict(store.category_counts())
                categories = [
                    CategoryItem(
                        id=str(spec.id),
                        name=str(spec.name),
                        count=counts.get(str(spec.id), 0),
                    )
                    for spec in CATEGORY_CATALOG
                ]
                is_blocked = store.latest_crawl_status() is CrawlStatus.BLOCKED
        except (StorageError, sqlite3.Error) as error:
            return CategoryResponse(categories=[], error=storage_error(error))
        return CategoryResponse(
            categories=categories, error=blocked() if is_blocked else None
        )
