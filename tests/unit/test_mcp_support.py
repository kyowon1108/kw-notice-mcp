"""Unit tests for shared MCP response helpers."""

import pytest

from kw_notice_mcp.mcp_support import pagination


@pytest.mark.parametrize(
    ("item_count", "limit", "offset", "total", "expected"),
    [
        (50, 50, 450, 561, (500, True)),
        (50, 50, 500, 561, (None, False)),
        (1, 1, 499, 561, (500, True)),
        (1, 1, 500, 561, (None, False)),
    ],
)
def test_pagination_never_advertises_an_offset_outside_the_input_contract(
    item_count: int,
    limit: int,
    offset: int,
    total: int,
    expected: tuple[int | None, bool],
) -> None:
    """The final addressable page must terminate even when older rows exist."""
    assert pagination(item_count, limit, offset, total) == expected
