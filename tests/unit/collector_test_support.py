"""Deterministic collector-test support without pytest discovery."""

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kw_notice_mcp.collector import Collector
from kw_notice_mcp.storage import open_storage
from kw_notice_mcp.wire import WireBudget, WireResponse

FIXTURES = Path(__file__).parents[1] / "fixtures"
ROBOTS = b"User-agent: *\nAllow: /ko/life/notice.jsp\n"


class FakeWire:
    """A deterministic transport whose calls are the test observable."""

    responses: list[WireResponse | BaseException]

    def __init__(self, responses: list[WireResponse | BaseException]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    async def request(self, url: str) -> WireResponse:
        self.calls.append(url)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class FakeClock:
    """A monotonic and wall clock controlled by the test."""

    current: datetime

    def __init__(self) -> None:
        self.current = datetime(2026, 7, 5, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current


class FakeSleeper:
    """A sleeper that records policy delays without waiting."""

    clock: FakeClock | None

    def __init__(self, clock: FakeClock | None = None) -> None:
        """Optionally advance an injected clock by each requested delay."""
        self.delays: list[float] = []
        self.clock = clock

    async def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)
        if self.clock is not None:
            self.clock.current += timedelta(seconds=seconds)


def response(status: int, body: bytes, **headers: str) -> WireResponse:
    """Build a wire response with terse call sites."""
    return WireResponse(status_code=status, headers=headers, body=body)


@contextmanager
def collector(
    database: Path,
    wire: FakeWire,
    sleeper: FakeSleeper,
    clock: FakeClock,
    *,
    budget: WireBudget | None = None,
) -> Generator[Collector]:
    """Open storage and inject all collector side-effect boundaries."""
    with open_storage(database) as store:
        yield Collector(
            store=store,
            transport=wire,
            sleeper=sleeper.sleep,
            clock=clock.now,
            budget=budget,
        )
