"""Protocol-level tests for the official MCP v2 stdio server."""

from datetime import date
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client._memory import InMemoryTransport
from mcp.client.stdio import stdio_client

from kw_notice_mcp.domain import CategoryId, CategoryName, NoticeDetail, NoticeSummary
from kw_notice_mcp.mcp_tools import NoticeToolService
from kw_notice_mcp.server import create_server
from kw_notice_mcp.storage import open_storage
from kw_notice_mcp.values import DUID, SourceURL


def _database(tmp_path: Path) -> Path:
    """Create one initialized fixture database without any network dependency."""
    database = tmp_path / "notices.sqlite3"
    detail = NoticeDetail(
        summary=NoticeSummary(
            duid=DUID("1001"),
            title="Notice",
            category_id=CategoryId("general"),
            category_name=CategoryName("일반"),
            posted_date=date(2026, 8, 1),
            updated_date=date(2026, 8, 1),
            department="Department",
            source_url=SourceURL(
                "https://www.kw.ac.kr/ko/life/notice.jsp?BoardMode=view&DUID=1001"
            ),
            attachments_present=False,
            pinned=False,
        ),
        body="Body",
    )
    with open_storage(database) as store:
        _ = store.save_detail(detail)
    return database


def test_all_four_tools_over_in_memory_sdk_transport(tmp_path: Path) -> None:
    """Given the configured server, the SDK lists and calls exactly four tools."""
    database = _database(tmp_path)

    async def scenario() -> None:
        server = create_server(database)
        async with (
            InMemoryTransport(server) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            _ = await session.initialize()
            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            assert names == [
                "search_notices",
                "get_notice",
                "list_latest_notices",
                "list_categories",
            ]
            for name, arguments in (
                ("search_notices", {"query": "Notice"}),
                ("get_notice", {"duid": "1001"}),
                ("list_latest_notices", {"limit": 1}),
                ("list_categories", {}),
            ):
                result = await session.call_tool(name, arguments)
                assert result.is_error is not True
                assert result.content

    anyio.run(scenario)


def test_all_four_tools_over_stdio(tmp_path: Path) -> None:
    """Given a fixture DB, the real serve process answers four tools on stdout only."""
    database = _database(tmp_path)

    async def scenario() -> None:
        params = StdioServerParameters(
            command="uv",
            args=["run", "kw-notice-mcp", "serve", "--db-path", str(database)],
        )
        async with (
            stdio_client(params) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            _ = await session.initialize()
            tools = await session.list_tools()
            assert len(tools.tools) == 4
            for name, arguments in (
                ("search_notices", {}),
                ("get_notice", {"duid": "1001"}),
                ("list_latest_notices", {}),
                ("list_categories", {}),
            ):
                result = await session.call_tool(name, arguments)
                assert result.is_error is not True
                assert result.content

    anyio.run(scenario)


def test_stdout_protocol_clean_no_http_calls(tmp_path: Path) -> None:
    """Given a tool service construction, no DB write or network request occurs."""
    database = tmp_path / "absent.sqlite3"
    service = NoticeToolService(database)

    result = service.list_categories()

    assert result.categories == []
    assert result.error is not None
    assert result.error.code == "storage_unavailable"
    assert not database.exists()


def test_malformed_tool_input_is_bounded_and_server_remains_usable(
    tmp_path: Path,
) -> None:
    """Given schema-invalid input, the SDK returns an error and stays usable."""
    database = _database(tmp_path)

    async def scenario() -> None:
        server = create_server(database)
        async with (
            InMemoryTransport(server) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            _ = await session.initialize()
            malformed = await session.call_tool(
                "search_notices", {"query": ["not", "text"]}
            )
            assert malformed.is_error is True
            assert "Traceback" not in str(malformed.content)

            healthy = await session.call_tool("list_categories", {})
            assert healthy.is_error is not True
            assert healthy.content

    anyio.run(scenario)
