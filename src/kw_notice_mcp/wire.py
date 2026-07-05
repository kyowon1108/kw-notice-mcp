"""Bounded HTTP wire contracts and the production httpx2 adapter."""

import socket
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import TracebackType
from typing import Final, Protocol, Self
from urllib.parse import urljoin, urlsplit

import anyio
import httpx2

MAX_RESPONSE_BYTES: Final = 4 * 1024 * 1024
MAX_ATTEMPTS: Final = 3
MAX_WIRE_REQUESTS: Final = 500
CONNECT_TIMEOUT: Final = 5.0
READ_TIMEOUT: Final = 30.0
WRITE_TIMEOUT: Final = 10.0
POOL_TIMEOUT: Final = 10.0

_LIMITS = httpx2.Limits(
    max_connections=4,
    max_keepalive_connections=2,
    keepalive_expiry=30.0,
)
_TIMEOUT = httpx2.Timeout(
    connect=CONNECT_TIMEOUT,
    read=READ_TIMEOUT,
    write=WRITE_TIMEOUT,
    pool=POOL_TIMEOUT,
)
_SOCKET_OPTIONS: Final[list[tuple[int, int, int]]] = [
    (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
]


class WireRole(StrEnum):
    """Allowlisted request roles with distinct path policies."""

    ROBOTS = "robots"
    NOTICE = "notice"


class TargetIssueCode(StrEnum):
    """Safe URL rejection categories recorded without URL bodies."""

    MALFORMED = "malformed_target"
    SCHEME = "invalid_scheme"
    HOST = "invalid_host"
    PORT = "invalid_port"
    USERINFO = "userinfo"
    FRAGMENT = "fragment"
    PATH = "disallowed_path"


@dataclass(frozen=True, slots=True)
class TargetIssue:
    """A URL validation result that never reaches the transport."""

    code: TargetIssueCode


def validate_wire_target(  # noqa: PLR0911
    raw: str, role: WireRole
) -> str | TargetIssue:
    """Validate scheme, authority, and role path before every request."""
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return TargetIssue(TargetIssueCode.MALFORMED)
    if parts.scheme != "https":
        return TargetIssue(TargetIssueCode.SCHEME)
    if parts.hostname != "www.kw.ac.kr":
        return TargetIssue(TargetIssueCode.HOST)
    if parts.username is not None or parts.password is not None:
        return TargetIssue(TargetIssueCode.USERINFO)
    if port not in (None, 443):
        return TargetIssue(TargetIssueCode.PORT)
    if parts.fragment:
        return TargetIssue(TargetIssueCode.FRAGMENT)
    expected = "/robots.txt" if role is WireRole.ROBOTS else "/ko/life/notice.jsp"
    if parts.path != expected:
        return TargetIssue(TargetIssueCode.PATH)
    return raw


def redirect_target(current: str, location: str, role: WireRole) -> str | TargetIssue:
    """Resolve and validate one redirect before permitting its next request."""
    return validate_wire_target(urljoin(current, location), role)


@dataclass(frozen=True, slots=True)
class WireResponse:
    """A bounded response with no streaming or raw persistence after return."""

    status_code: int
    headers: Mapping[str, str]
    body: bytes

    @property
    def location(self) -> str | None:
        """Return a redirect target without exposing the response body."""
        return self.headers.get("location") or self.headers.get("Location")


class WireTransport(Protocol):
    """Minimal injectable transport used by the collector."""

    async def request(self, url: str) -> WireResponse:
        """Perform exactly one already-validated GET request."""
        ...


class Sleeper(Protocol):
    """Async delay boundary used for deterministic rate-limit tests."""

    def __call__(self, seconds: float, /) -> Awaitable[None]:
        """Sleep for a bounded policy delay."""
        ...


class WireError(ConnectionError):
    """Base class for safe transport failures."""


class WireConnectionError(WireError):
    """A connection or timeout failure eligible for bounded retry."""


class ResponseTooLargeError(WireError):
    """A response exceeded the four mebibyte streaming cap."""


class WireBudget:
    """Mutable global request budget owned by one collector run."""

    maximum: int
    used: int

    def __init__(self, maximum: int = MAX_WIRE_REQUESTS) -> None:
        """Create a hard budget; mutation is required to reserve requests."""
        self.maximum = maximum
        self.used = 0

    def consume(self) -> None:
        """Reserve one request, refusing to cross the hard maximum."""
        if self.used >= self.maximum:
            raise WireBudgetExceededError
        self.used += 1


class WireBudgetExceededError(WireError):
    """The next wire request would exceed the global request budget."""


WireBudgetExceeded = WireBudgetExceededError


def create_httpx_client(
    user_agent: str = "kw-notice-mcp/0.1 (+local metadata collector)",
) -> httpx2.AsyncClient:
    """Create the tuned HTTP/2 client used for real collection.

    Retry ownership remains in the collector so every attempt can be delayed and
    counted.  The transport therefore has no hidden retries.
    """
    transport = httpx2.AsyncHTTPTransport(
        http2=True,
        retries=0,
        limits=_LIMITS,
        socket_options=_SOCKET_OPTIONS,
    )
    return httpx2.AsyncClient(
        transport=transport,
        timeout=_TIMEOUT,
        headers={"User-Agent": user_agent},
        follow_redirects=False,
    )


class HttpxWireTransport:
    """httpx2 adapter that streams and bounds one response."""

    def __init__(self, client: httpx2.AsyncClient | None = None) -> None:
        """Create an adapter, optionally borrowing a caller-owned client."""
        self._client: httpx2.AsyncClient = client or create_httpx_client()
        self._owns_client: bool = client is None

    async def request(self, url: str) -> WireResponse:
        """Fetch one response and reject oversized bodies before returning."""
        try:
            async with self._client.stream("GET", url) as response:
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise ResponseTooLargeError  # noqa: TRY301
                    chunks.append(chunk)
                return WireResponse(
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    body=b"".join(chunks),
                )
        except ResponseTooLargeError:
            raise
        except (httpx2.RequestError, httpx2.StreamError) as error:
            raise WireConnectionError from error

    async def aclose(self) -> None:
        """Close the client only when this adapter created it."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> Self:
        """Enter the adapter context."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close owned resources at context exit."""
        del exc_type, exc, traceback
        await self.aclose()


async def anyio_sleeper(seconds: float) -> None:
    """Sleep through AnyIO without binding the collector to asyncio."""
    await anyio.sleep(seconds)
