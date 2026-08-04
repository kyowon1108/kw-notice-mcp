"""Detail-page parsing and DOM minimization for KW notices."""

from bs4 import BeautifulSoup
from bs4.element import Tag

from kw_notice_mcp.domain import (
    DetailParseResult,
    NoticeDetail,
    NoticeSummary,
    ParseIssue,
    ParseIssueCode,
    category_from_name,
)
from kw_notice_mcp.parsing_shared import (
    BODY_SELECTORS,
    CATEGORY_SELECTORS,
    DEPARTMENT_SELECTORS,
    POSTED_SELECTORS,
    ROW_SELECTORS,
    UPDATED_SELECTORS,
    first_text,
    issue,
    parse_date,
)
from kw_notice_mcp.redaction import redact_human_fields
from kw_notice_mcp.source import build_detail_url, parse_duid
from kw_notice_mcp.values import DUID

_REMOVABLE_SELECTORS = (
    "a",
    "iframe",
    "embed",
    "object",
    ".attachment",
    ".file",
    ".download",
)


def _summary(
    soup: BeautifulSoup, duid: DUID
) -> tuple[NoticeSummary | None, ParseIssue | None]:
    """Parse redacted detail metadata from the source tree."""
    root = soup.select_one("article, main, body")
    if root is None:
        return None, issue(ParseIssueCode.MALFORMED_MARKUP, "detail")
    title = first_text(root, ROW_SELECTORS)
    category_name = first_text(root, CATEGORY_SELECTORS)
    category = category_from_name(category_name)
    if not title:
        return None, issue(ParseIssueCode.MISSING_TITLE, "detail")
    if category is None:
        code = (
            ParseIssueCode.MISSING_CATEGORY
            if not category_name
            else ParseIssueCode.UNKNOWN_CATEGORY
        )
        return None, issue(code, "detail")
    posted, posted_issue = parse_date(
        first_text(root, POSTED_SELECTORS), "detail-posted"
    )
    if posted_issue is not None or posted is None:
        return None, posted_issue
    updated, updated_issue = parse_date(
        first_text(root, UPDATED_SELECTORS), "detail-updated"
    )
    if updated_issue is not None:
        updated = posted
    fields = redact_human_fields(
        title=title,
        category_name=category.name,
        department=first_text(root, DEPARTMENT_SELECTORS),
        body="",
    )
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
            attachments_present=False,
            pinned=False,
        ),
        None,
    )


def _body(root: Tag, summary: NoticeSummary) -> tuple[str, bool]:
    """Remove link/embed nodes and return redacted body plus presence flag."""
    body_root = root.select_one(", ".join(BODY_SELECTORS))
    if body_root is None:
        return "", False
    attachments_present = bool(
        body_root.select("a[href], iframe[src], embed[src], object[data], .attachment")
    )
    for removable in body_root.select(", ".join(_REMOVABLE_SELECTORS)):
        removable.decompose()
    return (
        redact_human_fields(
            title=summary.title,
            category_name=summary.category_name,
            department=summary.department,
            body=body_root.get_text(" ", strip=True),
        ).body,
        attachments_present,
    )


def parse_detail_soup(soup: BeautifulSoup, raw_duid: str) -> DetailParseResult:
    """Parse one detail tree with typed malformed-DUID outcomes."""
    duid = parse_duid(raw_duid)
    if duid is None:
        code = (
            ParseIssueCode.MISSING_DUID
            if not raw_duid
            else ParseIssueCode.MALFORMED_DUID
        )
        return DetailParseResult(None, (issue(code, "detail"),))
    summary, summary_issue = _summary(soup, duid)
    if summary_issue is not None or summary is None:
        issues: tuple[ParseIssue, ...] = (
            () if summary_issue is None else (summary_issue,)
        )
        return DetailParseResult(None, issues)
    root = soup.select_one("article, main, body")
    if root is None:
        return DetailParseResult(
            None, (issue(ParseIssueCode.MALFORMED_MARKUP, "detail"),)
        )
    body, attachments_present = _body(root, summary)
    detail_summary = NoticeSummary(
        duid=summary.duid,
        title=summary.title,
        category_id=summary.category_id,
        category_name=summary.category_name,
        posted_date=summary.posted_date,
        updated_date=summary.updated_date,
        department=summary.department,
        source_url=summary.source_url,
        attachments_present=attachments_present,
        pinned=summary.pinned,
    )
    return DetailParseResult(NoticeDetail(detail_summary, body), ())
