"""Typed Pydantic response contracts for the four public MCP tools."""

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

ErrorCode = Literal[
    "invalid_input",
    "not_found",
    "tombstoned",
    "storage_unavailable",
    "blocked",
]
FreshnessValue = Literal["fresh", "stale", "expired"]


class _ResponseModel(BaseModel):
    """Base response configuration that prevents undocumented output fields."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class ErrorEnvelope(_ResponseModel):
    """A structured, non-traceback tool outcome."""

    code: ErrorCode
    message: str
    retryable: bool


class NoticeSummary(_ResponseModel):
    """The bounded, redacted metadata projection returned to MCP clients."""

    duid: str
    title: str
    category_id: str
    category: str
    posted_date: str
    updated_date: str
    department: str
    source_url: str
    attachments_present: bool
    collected_at: str
    freshness: FreshnessValue


class NoticeDetail(_ResponseModel):
    """A summary plus a retained, bounded redacted body."""

    summary: NoticeSummary
    body: str | None


class SearchResponse(_ResponseModel):
    """The fixed search response shape."""

    items: list[NoticeSummary]
    query: str
    limit: int
    offset: int
    next_offset: int | None
    has_more: bool
    error: ErrorEnvelope | None


class NoticeResponse(_ResponseModel):
    """The fixed single-notice response shape."""

    notice: NoticeDetail | None
    error: ErrorEnvelope | None


class LatestResponse(_ResponseModel):
    """The fixed latest-notices response shape."""

    items: list[NoticeSummary]
    limit: int
    offset: int
    next_offset: int | None
    has_more: bool
    error: ErrorEnvelope | None


class CategoryItem(_ResponseModel):
    """One canonical category and its non-tombstoned notice count."""

    id: str
    name: str
    count: int


class CategoryResponse(_ResponseModel):
    """The fixed category response shape."""

    categories: list[CategoryItem]
    error: ErrorEnvelope | None
