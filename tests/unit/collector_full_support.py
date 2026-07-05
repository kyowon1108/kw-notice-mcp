"""Shared deterministic fixtures for FULL collector regression scenarios."""

from kw_notice_mcp.wire import WireResponse

from .collector_test_support import (
    FIXTURES,
    ROBOTS,
    FakeWire,
    response,
)


def two_notice_list() -> bytes:
    """Build two distinct changed list records for one bounded FULL page."""
    return """
    <main>
      <section class="board-list-box">
        <a class="title" href="/ko/life/notice.jsp?DUID=1001">Changed one</a>
        <span class="category">학사</span>
        <span class="posted-date">2026.08.01</span>
        <span class="updated-date">2026.08.03</span>
        <span class="department">Academic Office</span>
      </section>
      <section class="board-list-box">
        <a class="title" href="/ko/life/notice.jsp?DUID=1002">Changed two</a>
        <span class="category">등록/장학</span>
        <span class="posted-date">2026.07.31</span>
        <span class="updated-date">2026.08.03</span>
        <span class="department">Student Support</span>
      </section>
    </main>
    """.encode()


def detail_body() -> bytes:
    """Return the local detail fixture used by every injected detail request."""
    return (FIXTURES / "notice_detail_minimal.html").read_bytes()


def full_wire(*details: bytes | BaseException | WireResponse) -> FakeWire:
    """Build a robots, index, and detail response sequence without live I/O."""
    return FakeWire(
        [
            response(200, ROBOTS, **{"content-type": "text/plain"}),
            response(200, two_notice_list(), **{"content-type": "text/html"}),
            *[
                item
                if isinstance(item, (BaseException, WireResponse))
                else response(200, item, **{"content-type": "text/html"})
                for item in details
            ],
        ]
    )
