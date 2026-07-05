"""Unit tests for the fail-closed robots boundary."""

from pathlib import Path

import pytest

from kw_notice_mcp.collector_models import CollectionMode
from kw_notice_mcp.robots import (
    RobotsBlockReason,
    parse_robots,
    parse_robots_response,
)
from kw_notice_mcp.settings import Settings

FIXTURES = Path(__file__).parents[1] / "fixtures"
CUSTOM_404 = (FIXTURES / "robots_custom_404_observed.html").read_bytes()


def test_valid_text_plain_robots_allows_notice_and_parses_delay() -> None:
    """Given valid text/plain rules, the notice path is permitted."""
    result = parse_robots(
        "text/plain; charset=utf-8",
        b"User-agent: *\nCrawl-delay: 1.5\nAllow: /ko/life/notice.jsp\n",
    )

    assert result.allowed is True
    assert result.crawl_delay == 1.5
    assert result.block_reason is None


def test_exact_custom_404_is_typed_robots_missing_and_metadata_only() -> None:
    """Given the exact bounded KW custom-404, permit metadata-only mode."""
    observed_crlf = CUSTOM_404.replace(b"\n", b"\r\n")
    result = parse_robots_response(200, "text/html; charset=utf-8", observed_crlf)

    assert result.allowed is False
    assert result.block_reason is RobotsBlockReason.ROBOTS_MISSING
    assert result.mode is CollectionMode.METADATA_ONLY


def test_generic_http_404_is_blocked() -> None:
    """Given any non-200 HTTP 404, fail closed without metadata authorization."""
    result = parse_robots_response(404, "text/html", b"not found")

    assert result.block_reason is RobotsBlockReason.HTTP_FAILURE
    assert result.mode is None


@pytest.mark.parametrize(
    "body",
    [
        b"<html><title>CAPTCHA challenge</title></html>",
        b"<html><title>Access Denied</title><body>WAF</body></html>",
        b"<html><body>Cloudflare security check</body></html>",
    ],
    ids=["captcha", "waf", "cloudflare"],
)
def test_challenge_markers_in_html_remain_blocked(body: bytes) -> None:
    """Given a challenge or WAF marker, do not authorize metadata collection."""
    result = parse_robots_response(200, "text/html", body)

    assert result.block_reason is RobotsBlockReason.CAPTCHA
    assert result.mode is None


def test_no_generic_policy_override_is_exposed() -> None:
    """Given the settings boundary, no bypass field exists."""
    assert "robots_" + "bypass" not in Settings.model_fields


def test_malformed_and_disallow_rules_fail_closed() -> None:
    """Given malformed or restrictive directives, the notice path is blocked."""
    malformed = parse_robots("text/plain", b"User-agent *\nDisallow: /\n")
    disallowed = parse_robots("text/plain", b"User-agent: *\nDisallow: /ko/life\n")

    assert malformed.block_reason is RobotsBlockReason.MALFORMED
    assert disallowed.block_reason is RobotsBlockReason.DISALLOWED


@pytest.mark.parametrize(
    "body",
    [
        b"User-agent: *\nDisallow: /ko/*\n",
        b"User-agent: *\nDisallow: /ko/life/notice.jsp$\n",
    ],
    ids=["wildcard", "terminal-anchor"],
)
def test_standard_path_rules_block_wildcard_and_exact_anchor(body: bytes) -> None:
    """Given standard path syntax, matching denials block the notice path."""
    result = parse_robots("text/plain", body)

    assert result.allowed is False
    assert result.block_reason is RobotsBlockReason.DISALLOWED


@pytest.mark.parametrize(
    ("body", "expected_allowed"),
    [
        (
            b"User-agent: *\nDisallow: /ko/*\nAllow: /ko/life/notice.jsp\n",
            True,
        ),
        (
            b"User-agent: *\nDisallow: /ko/life/notice.jsp\nAllow: /ko/*\n",
            False,
        ),
        (
            b"User-agent: *\nDisallow: /ko/*\nAllow: /ko/life/notice.jsp$\n",
            True,
        ),
    ],
    ids=["longer-allow", "longer-disallow", "equal-anchor-allow"],
)
def test_longest_matching_rule_controls_allowance(
    body: bytes, expected_allowed: bool
) -> None:
    """Given competing rules, the longest matching rule controls the result."""
    result = parse_robots("text/plain", body)

    assert result.allowed is expected_allowed
