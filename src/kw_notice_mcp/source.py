"""Allowlisted KW source URL construction and validation."""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, override
from urllib.parse import SplitResult, parse_qsl, urlsplit

from kw_notice_mcp.values import DUID, SourceURL

NOTICE_HOST: Final = "www.kw.ac.kr"
NOTICE_PATH: Final = "/ko/life/notice.jsp"
NOTICE_ORIGIN: Final = f"https://{NOTICE_HOST}"
_DUID_PATTERN: Final = re.compile(r"[0-9]{1,12}\Z")
_LIST_QUERY: Final = "srCategoryId=&mode=list&searchKey=1&searchVal=&tpage="
_DETAIL_QUERY: Final = "BoardMode=view&DUID="
_LIST_QUERY_FIELDS: Final = (
    ("srCategoryId", ""),
    ("mode", "list"),
    ("searchKey", "1"),
    ("searchVal", ""),
)
_DETAIL_QUERY_FIELDS: Final = (("BoardMode", "view"),)


class SourceURLIssueCode(StrEnum):
    """Typed reasons for rejecting an untrusted source URL."""

    MALFORMED_URL = "malformed_url"
    WRONG_SCHEME = "wrong_scheme"
    WRONG_HOST = "wrong_host"
    WRONG_PATH = "wrong_path"
    WRONG_QUERY = "wrong_query"


@dataclass(frozen=True, slots=True)
class SourceURLIssue:
    """A safe source URL validation failure."""

    code: SourceURLIssueCode


@dataclass(frozen=True, slots=True)
class InvalidSourceParameterError(ValueError):
    """A source URL constructor argument outside the bounded contract."""

    parameter: str
    value: str

    @override
    def __str__(self) -> str:
        """Describe the invalid constructor argument."""
        return f"invalid source parameter: {self.parameter}={self.value!r}"


def parse_duid(raw: str) -> DUID | None:
    """Parse an ASCII decimal DUID into its branded representation."""
    if _DUID_PATTERN.fullmatch(raw) is None:
        return None
    return DUID(raw)


def duid_from_href(href: str) -> DUID | None:
    """Extract a decimal DUID query value without trusting the source href."""
    try:
        query = dict(parse_qsl(urlsplit(href).query, keep_blank_values=True))
    except ValueError:
        return None
    raw_duid = query.get("DUID")
    if raw_duid is None:
        return None
    return parse_duid(raw_duid)


def build_list_url(page: int) -> SourceURL:
    """Construct the exact allowlisted list query for one positive page."""
    if page < 1:
        parameter = "page"
        raise InvalidSourceParameterError(parameter, str(page))
    return SourceURL(f"{NOTICE_ORIGIN}{NOTICE_PATH}?{_LIST_QUERY}{page}")


def build_detail_url(duid: DUID) -> SourceURL:
    """Construct the exact allowlisted detail query for a branded DUID."""
    if _DUID_PATTERN.fullmatch(duid) is None:
        parameter = "DUID"
        raise InvalidSourceParameterError(parameter, duid)
    return SourceURL(f"{NOTICE_ORIGIN}{NOTICE_PATH}?BoardMode=view&DUID={duid}")


def _parts_issue(parts: SplitResult, port: int | None) -> SourceURLIssue | None:
    """Return a structural URL issue after urlsplit has succeeded."""
    if parts.scheme != "https":
        return SourceURLIssue(SourceURLIssueCode.WRONG_SCHEME)
    if parts.hostname != NOTICE_HOST or parts.username or parts.password:
        return SourceURLIssue(SourceURLIssueCode.WRONG_HOST)
    if port not in (None, 443):
        return SourceURLIssue(SourceURLIssueCode.WRONG_HOST)
    if parts.path != NOTICE_PATH or parts.fragment:
        return SourceURLIssue(SourceURLIssueCode.WRONG_PATH)
    return None


def _validate_query(parts: SplitResult, raw: str) -> SourceURL | SourceURLIssue:
    """Validate the exact list or detail query shape."""
    try:
        query = tuple(
            parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
        )
    except ValueError:
        return SourceURLIssue(SourceURLIssueCode.WRONG_QUERY)
    if len(query) == len(_LIST_QUERY_FIELDS) + 1 and query[:-1] == _LIST_QUERY_FIELDS:
        page_key, page = query[-1]
        exact_query = f"{_LIST_QUERY}{page}"
        if (
            page_key == "tpage"
            and page.isdecimal()
            and page.isascii()
            and int(page) >= 1
            and parts.query == exact_query
        ):
            return SourceURL(raw)
    elif (
        len(query) == len(_DETAIL_QUERY_FIELDS) + 1
        and query[:-1] == _DETAIL_QUERY_FIELDS
    ):
        duid_key, duid = query[-1]
        if (
            duid_key == "DUID"
            and parse_duid(duid) is not None
            and parts.query == f"{_DETAIL_QUERY}{duid}"
        ):
            return SourceURL(raw)
    return SourceURLIssue(SourceURLIssueCode.WRONG_QUERY)


def validate_source_url(raw: str) -> SourceURL | SourceURLIssue:
    """Validate a constructed list/detail URL and reject arbitrary markup URLs."""
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return SourceURLIssue(SourceURLIssueCode.MALFORMED_URL)
    issue = _parts_issue(parts, port)
    return issue if issue is not None else _validate_query(parts, raw)
