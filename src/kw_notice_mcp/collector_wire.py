"""Request sequencing helpers for the collector's counted wire boundary."""

from dataclasses import dataclass

import httpx2

from kw_notice_mcp.collector_context import CollectorCounters
from kw_notice_mcp.robots import contains_challenge_marker
from kw_notice_mcp.wire import (
    MAX_ATTEMPTS,
    MAX_RESPONSE_BYTES,
    ResponseTooLargeError,
    Sleeper,
    WireBudget,
    WireConnectionError,
    WireResponse,
    WireRole,
    WireTransport,
    redirect_target,
    validate_wire_target,
)

MAX_REDIRECT_HOPS = 5
HTTP_OK = 200
HTTP_NOT_FOUND = 404
HTTP_REDIRECT_START = 300
HTTP_REDIRECT_END = 400
HTTP_FORBIDDEN = 403
HTTP_RATE_LIMIT = 429


@dataclass(frozen=True, slots=True)
class WireRequestResult:
    """Internal logical request result after attempts and redirect hops."""

    response: WireResponse | None
    reason: str | None
    requests: int
    retries: int


def content_type(response: WireResponse) -> str | None:
    """Read a case-insensitive response media type."""
    for key, value in response.headers.items():
        if key.lower() == "content-type":
            return value.split(";", 1)[0].strip().lower()
    return None


def contains_captcha(body: bytes) -> bool:
    """Detect challenge markers without logging or persisting response content."""
    return contains_challenge_marker(body)


def decode_html(body: bytes) -> str | None:
    """Decode page bytes transiently, returning None for malformed UTF-8."""
    try:
        return body.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None


def _status_reason(status_code: int) -> str:
    """Convert an HTTP status into a safe collector reason."""
    if status_code == HTTP_FORBIDDEN:
        return "forbidden"
    if status_code == HTTP_RATE_LIMIT:
        return "rate_limit"
    return "http_failure"


async def request_with_policy(  # noqa: C901, PLR0911, PLR0912, PLR0913
    transport: WireTransport,
    budget: WireBudget,
    sleeper: Sleeper,
    url: str,
    role: WireRole,
    delay: float,
    counters: CollectorCounters | None = None,
    detail: bool = False,
) -> WireRequestResult:
    """Perform exactly three retryable attempts and at most five redirect hops."""
    target = validate_wire_target(url, role)
    if not isinstance(target, str):
        return WireRequestResult(None, target.code.value, 0, 0)
    current = target
    retries = 0
    requests = 0
    detail_recorded = False
    for hop in range(MAX_REDIRECT_HOPS + 1):
        for attempt in range(MAX_ATTEMPTS):
            budget.consume()
            await sleeper(max(2.0, delay))
            requests += 1
            if counters is not None:
                counters.record_wire_request()
                if detail and not detail_recorded:
                    counters.record_detail()
                    detail_recorded = True
            try:
                response = await transport.request(current)
            except ResponseTooLargeError:
                raise
            except (
                WireConnectionError,
                httpx2.RequestError,
                httpx2.StreamError,
                ConnectionError,
                TimeoutError,
            ):
                if attempt + 1 == MAX_ATTEMPTS:
                    return WireRequestResult(
                        None, "transport_failure", requests, retries
                    )
                retries += 1
                if counters is not None:
                    counters.record_retry()
                continue
            if len(response.body) > MAX_RESPONSE_BYTES:
                raise ResponseTooLargeError
            if contains_captcha(response.body):
                return WireRequestResult(None, "captcha", requests, retries)
            if HTTP_REDIRECT_START <= response.status_code < HTTP_REDIRECT_END:
                location = response.location
                if not location:
                    return WireRequestResult(
                        None, "invalid_redirect_target", requests, retries
                    )
                if hop == MAX_REDIRECT_HOPS:
                    return WireRequestResult(
                        None, "redirect_hop_cap", requests, retries
                    )
                next_target = redirect_target(current, location, role)
                if not isinstance(next_target, str):
                    reason = (
                        "cross_host_redirect"
                        if next_target.code.value == "invalid_host"
                        else "disallowed_redirect_path"
                        if next_target.code.value == "disallowed_path"
                        else "invalid_redirect_target"
                    )
                    return WireRequestResult(None, reason, requests, retries)
                current = next_target
                break
            if response.status_code == HTTP_NOT_FOUND:
                return WireRequestResult(response, None, requests, retries)
            if (
                response.status_code < HTTP_OK
                or response.status_code >= HTTP_REDIRECT_START
            ):
                return WireRequestResult(
                    None, _status_reason(response.status_code), requests, retries
                )
            return WireRequestResult(response, None, requests, retries)
        else:
            return WireRequestResult(None, "redirect_hop_cap", requests, retries)
    return WireRequestResult(None, "redirect_hop_cap", requests, retries)
