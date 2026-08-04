"""Todo 7 fixture lifecycle from fake collection to an actual STDIO process."""

import os
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from kw_notice_mcp.collector import CollectionMode, Collector, CollectorConfig
from kw_notice_mcp.collector_models import CollectStatus
from kw_notice_mcp.storage import open_storage
from kw_notice_mcp.values import DUID
from kw_notice_mcp.wire import WireResponse

FIXTURES = Path(__file__).parents[1] / "fixtures"
ROBOTS = b"User-agent: *\nAllow: /ko/life/notice.jsp\n"


class _FixtureWire:
    """Return only robots and page-one fixture responses."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def request(self, url: str) -> WireResponse:
        """Return the response for one allowlisted fixture URL."""
        self.calls.append(url)
        if url.endswith("robots.txt"):
            return WireResponse(200, {"content-type": "text/plain"}, ROBOTS)
        return WireResponse(
            200,
            {"content-type": "text/html"},
            (FIXTURES / "board_list_minimal.html").read_bytes(),
        )


def test_fixture_collection_reaches_all_four_stdio_tools_without_network(
    tmp_path: Path,
) -> None:
    """Given a fake refresh, the actual MCP child serves fresh redacted cache data."""
    database = tmp_path / "notices.sqlite3"
    wire = _FixtureWire()

    async def collect() -> None:
        with open_storage(database) as store:
            result = await Collector(store=store, transport=wire).run(
                run_id="todo7-e2e",
                config=CollectorConfig(mode=CollectionMode.METADATA_ONLY),
            )
            assert result.status is CollectStatus.SUCCESS
            assert wire.calls == [
                "https://www.kw.ac.kr/robots.txt",
                "https://www.kw.ac.kr/ko/life/notice.jsp?srCategoryId=&mode=list&searchKey=1&searchVal=&tpage=1",
            ]
            assert store.freshness(DUID("1001")).value == "fresh"
            assert all(item.body is None for item in store.latest(limit=50))

    anyio.run(collect)

    guard = tmp_path / "sitecustomize.py"
    guard_source = """import socket
class _GuardedSocket(socket.socket):
    def connect(self, *args, **kwargs):
        raise RuntimeError("network forbidden in MCP child")
socket.socket = _GuardedSocket
"""
    _ = guard.write_text(
        guard_source,
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(tmp_path)

    async def serve() -> None:
        params = StdioServerParameters(
            command="uv",
            args=["run", "kw-notice-mcp", "serve", "--db-path", str(database)],
            env=environment,
        )
        async with (
            stdio_client(params) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            _ = await session.initialize()
            tools = await session.list_tools()
            assert [tool.name for tool in tools.tools] == [
                "search_notices",
                "get_notice",
                "list_latest_notices",
                "list_categories",
            ]
            search = await session.call_tool("search_notices", {})
            detail = await session.call_tool("get_notice", {"duid": "1001"})
            latest = await session.call_tool("list_latest_notices", {})
            categories = await session.call_tool("list_categories", {})
            assert all(
                result.content for result in (search, detail, latest, categories)
            )
            assert "Body" not in str(detail.content)
            assert "fresh" in str(search.content)

            with open_storage(database) as store:
                store.mark_detail_404(DUID("1001"))
            tombstoned = await session.call_tool("get_notice", {"duid": "1001"})
            assert "tombstoned" in str(tombstoned.content)

    anyio.run(serve)
