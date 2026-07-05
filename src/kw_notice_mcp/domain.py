"""Immutable domain contracts for KW notices and parser outcomes."""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final, NewType

from kw_notice_mcp.values import DUID, SourceURL

CategoryId = NewType("CategoryId", str)
CategoryName = NewType("CategoryName", str)


@dataclass(frozen=True, slots=True)
class CategorySpec:
    """One canonical returned category."""

    name: CategoryName
    id: CategoryId


CATEGORY_CATALOG: Final[tuple[CategorySpec, ...]] = (
    CategorySpec(CategoryName("일반"), CategoryId("general")),
    CategorySpec(CategoryName("학사"), CategoryId("academic")),
    CategorySpec(CategoryName("학생"), CategoryId("student")),
    CategorySpec(CategoryName("봉사"), CategoryId("volunteer")),
    CategorySpec(CategoryName("등록/장학"), CategoryId("registration-scholarship")),
    CategorySpec(CategoryName("입학"), CategoryId("admissions")),
    CategorySpec(CategoryName("시설"), CategoryId("facilities")),
    CategorySpec(CategoryName("병무"), CategoryId("military")),
    CategorySpec(CategoryName("외부"), CategoryId("external")),
    CategorySpec(CategoryName("국제교류"), CategoryId("international-exchange")),
    CategorySpec(CategoryName("국제학생"), CategoryId("international-student")),
)

_CATEGORIES_BY_NAME: Final[dict[str, CategorySpec]] = {
    item.name: item for item in CATEGORY_CATALOG
}
_CATEGORIES_BY_ID: Final[dict[str, CategorySpec]] = {
    item.id: item for item in CATEGORY_CATALOG
}


def category_from_name(name: str) -> CategorySpec | None:
    """Return a canonical category, excluding the crawl-only ``전체`` view."""
    return _CATEGORIES_BY_NAME.get(name.strip())


def category_from_id(category_id: str) -> CategorySpec | None:
    """Return a canonical category for an exact stored identifier."""
    return _CATEGORIES_BY_ID.get(category_id.strip())


class ParseIssueCode(StrEnum):
    """Typed parser failures that are safe to skip or report."""

    MALFORMED_MARKUP = "malformed_markup"
    MISSING_DUID = "missing_duid"
    MALFORMED_DUID = "malformed_duid"
    MISSING_TITLE = "missing_title"
    MISSING_CATEGORY = "missing_category"
    UNKNOWN_CATEGORY = "unknown_category"
    MISSING_DATE = "missing_date"
    MALFORMED_DATE = "malformed_date"


@dataclass(frozen=True, slots=True)
class ParseIssue:
    """One row/detail parse issue with no raw HTML retained."""

    code: ParseIssueCode
    location: str


@dataclass(frozen=True, slots=True)
class NoticeSummary:
    """Redacted metadata suitable for persistence or indexing."""

    duid: DUID
    title: str
    category_id: CategoryId
    category_name: CategoryName
    posted_date: date
    updated_date: date
    department: str
    source_url: SourceURL
    attachments_present: bool
    pinned: bool


@dataclass(frozen=True, slots=True)
class NoticeDetail:
    """A redacted notice summary plus normalized body text."""

    summary: NoticeSummary
    body: str


@dataclass(frozen=True, slots=True)
class ListParseResult:
    """All valid unique list records and typed issues found in source markup."""

    records: tuple[NoticeSummary, ...]
    issues: tuple[ParseIssue, ...]


@dataclass(frozen=True, slots=True)
class DetailParseResult:
    """A detail record or typed boundary issue."""

    notice: NoticeDetail | None
    issues: tuple[ParseIssue, ...]
