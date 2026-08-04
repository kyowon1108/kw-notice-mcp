"""Public parser facade for typed KW notice list and detail outcomes."""

from kw_notice_mcp.detail_parser import parse_detail_soup
from kw_notice_mcp.domain import DetailParseResult, ListParseResult, ParseIssueCode
from kw_notice_mcp.list_parser import parse_list_soup
from kw_notice_mcp.parsing_shared import boundary_or_issue, issue, soup_from_boundary


def parse_list_html(html: str) -> ListParseResult:
    """Parse list HTML with typed malformed-boundary and row outcomes."""
    boundary, boundary_issue = boundary_or_issue(html)
    if boundary_issue is not None or boundary is None:
        return ListParseResult(
            (), (boundary_issue or issue(ParseIssueCode.MALFORMED_MARKUP, "list"),)
        )
    return parse_list_soup(soup_from_boundary(boundary))


def parse_detail_html(html: str, raw_duid: str) -> DetailParseResult:
    """Parse detail HTML while discarding attachment/link/embed DOM content."""
    boundary, boundary_issue = boundary_or_issue(html)
    if boundary_issue is not None or boundary is None:
        return DetailParseResult(
            None, (boundary_issue or issue(ParseIssueCode.MALFORMED_MARKUP, "detail"),)
        )
    return parse_detail_soup(soup_from_boundary(boundary), raw_duid)
