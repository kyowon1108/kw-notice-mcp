"""Strict, fail-closed parsing for the KW robots policy."""

import hashlib
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from kw_notice_mcp.collector_models import CollectionMode

NOTICE_PATH = "/ko/life/notice.jsp"
MIN_DELAY_SECONDS: Final[float] = 2.0
HTTP_OK: Final[int] = 200
OBSERVED_CUSTOM_404_BYTES: Final[int] = 444
OBSERVED_CUSTOM_404_SHA256: Final[str] = (
    "71b4726aaed2b0261a8edb6bc5490abb83b3aa65f8f7fc6b3fd1d75d7a46d03c"
)
CHALLENGE_MARKERS: Final[tuple[bytes, ...]] = (
    b"captcha",
    b"recaptcha",
    b"robot check",
    b"waf",
    b"web application firewall",
    b"cloudflare",
    b"cf-chl-",
    b"incapsula",
    b"imperva",
    b"sucuri",
    b"akamai",
    b"access denied",
    b"security check",
    b"bot protection",
    b"bot detection",
    b"ddos protection",
)


class RobotsBlockReason(StrEnum):
    """Safe reasons a robots response cannot authorize collection."""

    HTTP_FAILURE = "robots_http_failure"
    INVALID_CONTENT_TYPE = "robots_invalid_content_type"
    MALFORMED = "robots_malformed"
    DISALLOWED = "robots_disallowed"
    ROBOTS_MISSING = "robots_missing"
    CAPTCHA = "robots_captcha"


@dataclass(frozen=True, slots=True)
class RobotsResult:
    """Parsed policy decision with only bounded directive metadata."""

    allowed: bool
    crawl_delay: float
    block_reason: RobotsBlockReason | None
    mode: CollectionMode | None


def _invalid(reason: RobotsBlockReason) -> RobotsResult:
    """Build a blocked result with the safe minimum delay."""
    return RobotsResult(
        allowed=False,
        crawl_delay=MIN_DELAY_SECONDS,
        block_reason=reason,
        mode=None,
    )


def _missing() -> RobotsResult:
    """Represent a known missing robots resource without granting full access."""
    return RobotsResult(
        allowed=False,
        crawl_delay=MIN_DELAY_SECONDS,
        block_reason=RobotsBlockReason.ROBOTS_MISSING,
        mode=CollectionMode.METADATA_ONLY,
    )


def _directive(  # noqa: PLR0911, PLR0913, PLR0917
    key: str,
    value: str,
    active: bool,
    delay: float,
    disallow: tuple[str, ...],
    allow: tuple[str, ...],
) -> tuple[bool, float, tuple[str, ...], tuple[str, ...], RobotsBlockReason | None]:
    """Apply one validated directive and return updated parser state."""
    if key == "user-agent":
        return value == "*", delay, disallow, allow, None
    if key == "disallow" and active:
        return active, delay, disallow + ((value,) if value else ()), allow, None
    if key == "allow" and active:
        return active, delay, disallow, allow + ((value,) if value else ()), None
    if key == "crawl-delay" and active:
        try:
            candidate = float(value)
        except ValueError:
            return active, delay, disallow, allow, RobotsBlockReason.MALFORMED
        if not math.isfinite(candidate) or candidate < 0:
            return active, delay, disallow, allow, RobotsBlockReason.MALFORMED
        return active, candidate, disallow, allow, None
    if key in {"allow", "crawl-delay", "disallow"}:
        return active, delay, disallow, allow, None
    if key in {"sitemap", "host"}:
        return active, delay, disallow, allow, None
    return active, delay, disallow, allow, RobotsBlockReason.MALFORMED


def _parse_lines(text: str) -> RobotsResult:
    """Parse the small standard directive subset needed by this collector."""
    in_wildcard = False
    saw_agent = False
    disallow: tuple[str, ...] = ()
    allow: tuple[str, ...] = ()
    delay = 0.0
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" not in line:
            return _invalid(RobotsBlockReason.MALFORMED)
        key, value = (part.strip() for part in line.split(":", 1))
        in_wildcard, delay, disallow, allow, problem = _directive(
            key.lower(), value, in_wildcard, delay, disallow, allow
        )
        saw_agent = saw_agent or (key.lower() == "user-agent" and value == "*")
        if problem is not None:
            return _invalid(problem)
    if not saw_agent:
        return _invalid(RobotsBlockReason.MALFORMED)
    matching_disallow = [rule for rule in disallow if _rule_matches(rule, NOTICE_PATH)]
    matching_allow = [rule for rule in allow if _rule_matches(rule, NOTICE_PATH)]
    if matching_disallow and max(map(len, matching_disallow)) > max(
        map(len, matching_allow), default=-1
    ):
        return _invalid(RobotsBlockReason.DISALLOWED)
    return RobotsResult(
        allowed=True,
        crawl_delay=max(0.0, delay),
        block_reason=None,
        mode=CollectionMode.FULL,
    )


def _rule_matches(rule: str, path: str) -> bool:
    """Match a robots path prefix with ``*`` and terminal ``$`` support."""
    anchored = rule.endswith("$")
    pattern = rule[:-1] if anchored else rule
    expression = re.escape(pattern).replace(r"\*", ".*")
    if anchored:
        return re.fullmatch(expression, path) is not None
    return re.match(f"^{expression}", path) is not None


def parse_robots(content_type: str | None, body: bytes) -> RobotsResult:
    """Parse a text/plain robots document and reject malformed input."""
    if (
        content_type is None
        or content_type.split(";", 1)[0].strip().lower() != "text/plain"
    ):
        return _invalid(RobotsBlockReason.INVALID_CONTENT_TYPE)
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return _invalid(RobotsBlockReason.MALFORMED)
    return _parse_lines(text)


def _is_custom_404(content_type: str | None, body: bytes) -> bool:
    """Match only the complete LF-canonicalized observed HTTP-200 document."""
    canonical = body.replace(b"\r\n", b"\n")
    return (
        content_type == "text/html"
        and len(canonical) == OBSERVED_CUSTOM_404_BYTES
        and hashlib.sha256(canonical).hexdigest() == OBSERVED_CUSTOM_404_SHA256
    )


def contains_challenge_marker(body: bytes) -> bool:
    """Detect bounded CAPTCHA or WAF markers without retaining response content."""
    lowered = body[:4096].lower()
    if b"waf" in lowered and re.search(rb"(?<![a-z0-9])waf(?![a-z0-9])", lowered):
        return True
    return any(marker in lowered for marker in CHALLENGE_MARKERS if marker != b"waf")


def parse_robots_response(
    status_code: int, content_type: str | None, body: bytes
) -> RobotsResult:
    """Classify one robots response, including the narrow metadata exception."""
    normalized_type = (
        content_type.split(";", 1)[0].strip().lower()
        if content_type is not None
        else None
    )
    if contains_challenge_marker(body):
        return _invalid(RobotsBlockReason.CAPTCHA)
    if status_code != HTTP_OK:
        return _invalid(RobotsBlockReason.HTTP_FAILURE)
    if _is_custom_404(normalized_type, body):
        return _missing()
    return parse_robots(normalized_type, body)
