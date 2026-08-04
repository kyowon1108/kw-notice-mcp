"""Shared typed helpers for HTML boundary parsing."""

import re
from datetime import date
from typing import ClassVar

from bs4 import BeautifulSoup
from bs4.element import Tag
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from kw_notice_mcp.domain import ParseIssue, ParseIssueCode

DATE_PATTERN = re.compile(r"\d{4}[./-]\d{1,2}[./-]\d{1,2}")
ROW_SELECTORS = (".title", ".notice-title", "[data-title]")
CATEGORY_SELECTORS = (".category", "[data-category]")
POSTED_SELECTORS = (".posted-date", ".reg-date", "[data-posted-date]")
UPDATED_SELECTORS = (".updated-date", ".modify-date", "[data-updated-date]")
DEPARTMENT_SELECTORS = (".department", ".writer", "[data-department]")
BODY_SELECTORS = (".body", ".board-body", ".view-content", "[data-body]")


class HtmlBoundary(BaseModel):
    """Pydantic boundary model for untrusted HTML input."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    raw_html: str = Field(min_length=1)


def boundary_or_issue(html: str) -> tuple[HtmlBoundary | None, ParseIssue | None]:
    """Parse HTML at the trust boundary and return a typed failure."""
    try:
        return HtmlBoundary(raw_html=html), None
    except ValidationError:
        return None, ParseIssue(ParseIssueCode.MALFORMED_MARKUP, "html")


def text(node: Tag | None) -> str:
    """Extract normalized raw text from one optional HTML node."""
    if node is None:
        return ""
    return node.get_text(" ", strip=True)


def attribute(node: Tag | None, name: str) -> str:
    """Read a scalar HTML attribute without leaking BeautifulSoup union types."""
    if node is None:
        return ""
    value = node.get(name)
    return value if isinstance(value, str) else ""


def first_text(node: Tag, selectors: tuple[str, ...]) -> str:
    """Return the first non-empty selected text value."""
    for selector in selectors:
        found = node.select_one(selector)
        value = text(found)
        if value:
            return value
    return ""


def issue(code: ParseIssueCode, location: str) -> ParseIssue:
    """Build one typed parser issue."""
    return ParseIssue(code=code, location=location)


def parse_date(value: str, location: str) -> tuple[date | None, ParseIssue | None]:
    """Parse a source date and classify missing or malformed values."""
    match = DATE_PATTERN.search(value)
    if match is None:
        code = (
            ParseIssueCode.MISSING_DATE if not value else ParseIssueCode.MALFORMED_DATE
        )
        return None, issue(code, location)
    normalized = match.group(0).replace(".", "-").replace("/", "-")
    try:
        return date.fromisoformat(normalized), None
    except ValueError:
        return None, issue(ParseIssueCode.MALFORMED_DATE, location)


def soup_from_boundary(boundary: HtmlBoundary) -> BeautifulSoup:
    """Create a parser tree only after boundary validation succeeds."""
    return BeautifulSoup(boundary.raw_html, "html.parser")
