"""List-page row parsing for the KW notice board."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from kw_notice_mcp.domain import (
    ListParseResult,
    NoticeSummary,
    ParseIssue,
    ParseIssueCode,
    category_from_name,
)
from kw_notice_mcp.parsing_shared import (
    CATEGORY_SELECTORS,
    DEPARTMENT_SELECTORS,
    POSTED_SELECTORS,
    ROW_SELECTORS,
    UPDATED_SELECTORS,
    attribute,
    direct_text,
    first_text,
    issue,
    parse_date,
    text,
)
from kw_notice_mcp.redaction import redact_human_fields
from kw_notice_mcp.source import build_detail_url, duid_from_href, parse_duid

if TYPE_CHECKING:
    from bs4 import BeautifulSoup
    from bs4.element import Tag

    from kw_notice_mcp.values import DUID

_INFO_POSTED_INDEX: Final = 1
_INFO_UPDATED_INDEX: Final = 2
_INFO_DEPARTMENT_INDEX: Final = 3


def _rows(soup: BeautifulSoup) -> list[Tag]:
    """Collect board-list-box rows, including pinned and ordinary variants."""
    rows: list[Tag] = []
    for box in soup.select(".board-list-box"):
        children = [
            child
            for child in box.select("li, tr, .board-row")
            if child.select_one("a[href]") is not None
        ]
        if children:
            rows.extend(children)
        elif text(box):
            rows.append(box)
    return rows


def _info_fields(row: Tag) -> tuple[str, str, str]:
    """Extract posted date, updated date, and department from live ``p.info``."""
    parts = tuple(text(row.select_one("p.info")).split("|"))
    return (
        parts[_INFO_POSTED_INDEX].strip() if len(parts) > _INFO_POSTED_INDEX else "",
        parts[_INFO_UPDATED_INDEX].strip() if len(parts) > _INFO_UPDATED_INDEX else "",
        parts[_INFO_DEPARTMENT_INDEX].strip()
        if len(parts) > _INFO_DEPARTMENT_INDEX
        else "",
    )


def _canonical_category_name(row: Tag) -> str:
    """Remove one outer ASCII bracket pair from a live list category."""
    raw_name = first_text(row, CATEGORY_SELECTORS)
    if raw_name.startswith("[") and raw_name.endswith("]"):
        return raw_name[1:-1].strip()
    return raw_name


def _row_record(row: Tag, index: int) -> tuple[NoticeSummary | None, ParseIssue | None]:
    """Parse one row without allowing malformed data to escape."""
    href_node = row.select_one("a[href*='DUID'], [data-duid]")
    href = attribute(href_node, "href")
    raw_duid = attribute(href_node, "data-duid")
    duid = duid_from_href(href) or parse_duid(raw_duid)
    if duid is None:
        code = (
            ParseIssueCode.MISSING_DUID
            if not raw_duid and "DUID=" not in href
            else ParseIssueCode.MALFORMED_DUID
        )
        return None, issue(code, f"list-row-{index}")
    title = first_text(row, ROW_SELECTORS) or direct_text(href_node)
    if not title:
        return None, issue(ParseIssueCode.MISSING_TITLE, f"list-row-{index}")
    category_name = _canonical_category_name(row)
    category = category_from_name(category_name)
    if category is None:
        code = (
            ParseIssueCode.MISSING_CATEGORY
            if not category_name
            else ParseIssueCode.UNKNOWN_CATEGORY
        )
        return None, issue(code, f"list-row-{index}")
    info_posted, info_updated, info_department = _info_fields(row)
    posted, posted_issue = parse_date(
        first_text(row, POSTED_SELECTORS) or info_posted,
        f"list-row-{index}-posted",
    )
    if posted_issue is not None or posted is None:
        return None, posted_issue
    updated, updated_issue = parse_date(
        first_text(row, UPDATED_SELECTORS) or info_updated,
        f"list-row-{index}-updated",
    )
    if updated_issue is not None:
        updated = posted
    fields = redact_human_fields(
        title=title,
        category_name=category.name,
        department=first_text(row, DEPARTMENT_SELECTORS) or info_department,
        body="",
    )
    pinned_text = f"{row.get('class')} {text(row.select_one('.kind'))}".lower()
    return (
        NoticeSummary(
            duid=duid,
            title=fields.title,
            category_id=category.id,
            category_name=category.name,
            posted_date=posted,
            updated_date=updated or posted,
            department=fields.department,
            source_url=build_detail_url(duid),
            attachments_present=row.select_one(".ico-file") is not None,
            pinned="notice" in pinned_text or "pinned" in pinned_text,
        ),
        None,
    )


def parse_list_soup(soup: BeautifulSoup) -> ListParseResult:
    """Parse unique list records from an already boundary-checked tree."""
    if soup.select_one(".board-list-box") is None:
        return ListParseResult(
            (),
            (issue(ParseIssueCode.MALFORMED_MARKUP, "list"),),
        )
    records: list[NoticeSummary] = []
    issues: list[ParseIssue] = []
    seen: set[DUID] = set()
    for index, row in enumerate(_rows(soup)):
        record, row_issue = _row_record(row, index)
        if row_issue is not None:
            issues.append(row_issue)
        elif record is not None and record.duid not in seen:
            seen.add(record.duid)
            records.append(record)
    return ListParseResult(tuple(records), tuple(issues))
