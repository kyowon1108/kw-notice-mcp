"""Immutable values returned by the SQLite storage repository."""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from kw_notice_mcp.domain import CategoryId, CategoryName
from kw_notice_mcp.values import DUID, SourceURL


class Freshness(StrEnum):
    """Cache freshness relative to a successful crawl and notice collection."""

    FRESH = "fresh"
    STALE = "stale"
    EXPIRED = "expired"


class CrawlStatus(StrEnum):
    """Persisted crawl lifecycle states."""

    RUNNING = "running"
    SUCCESS = "success"
    BLOCKED = "blocked"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class StoredNotice:
    """A notice row with body retention and lifecycle metadata."""

    duid: DUID
    category_id: CategoryId
    category_name: CategoryName
    title: str
    posted_date: date
    updated_date: date
    department: str
    source_url: SourceURL
    body: str | None
    body_expires_at: datetime | None
    content_hash: str
    attachments_present: bool
    collected_at: datetime
    tombstone_at: datetime | None
    source_status: str


@dataclass(frozen=True, slots=True)
class CrawlRun:
    """Restartable crawl state stored independently of notice mutations."""

    run_id: str
    status: CrawlStatus
    checkpoint_page: int
    pages_seen: int
    detail_requests: int
    index_requests: int
    retry_count: int
    block_reason: str | None
    started_at: datetime
    updated_at: datetime
    finished_at: datetime | None
