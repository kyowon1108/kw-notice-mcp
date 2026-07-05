"""Official MCP v2 STDIO server for the storage-only KW notice tools."""

from pathlib import Path

from mcp.server import MCPServer

from kw_notice_mcp.mcp_tools import NoticeToolService
from kw_notice_mcp.responses import (
    CategoryResponse,
    LatestResponse,
    NoticeResponse,
    SearchResponse,
)


def create_server(database: Path | str) -> MCPServer:
    """Create an MCPServer with exactly the four storage-only tools."""
    service = NoticeToolService(database)
    mcp = MCPServer("kw-notice-mcp", version="0.1.0")

    @mcp.tool()
    def search_notices(  # noqa: PLR0913, PLR0917
        query: str = "",
        category: str | None = None,
        published_from: str | None = None,
        published_to: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> SearchResponse:
        """Search redacted cached notices with bounded filters."""
        return service.search_notices(
            query, category, published_from, published_to, limit, offset
        )

    del search_notices

    @mcp.tool()
    def get_notice(duid: str) -> NoticeResponse:
        """Get one redacted cached notice by its decimal DUID."""
        return service.get_notice(duid)

    del get_notice

    @mcp.tool()
    def list_latest_notices(
        category: str | None = None, limit: int = 10, offset: int = 0
    ) -> LatestResponse:
        """List the latest cached notices by posted date."""
        return service.list_latest_notices(category, limit, offset)

    del list_latest_notices

    @mcp.tool()
    def list_categories() -> CategoryResponse:
        """List the fixed eleven categories and cached notice counts."""
        return service.list_categories()

    del list_categories

    return mcp
