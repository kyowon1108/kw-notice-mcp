"""Read-only notice queries shared by writable and MCP storage façades."""

import sqlite3
from datetime import date

from kw_notice_mcp.storage_models import StoredNotice
from kw_notice_mcp.storage_search import (
    category_counts,
    latest_count,
    latest_notices,
    search_count,
    search_notices,
)


class StorageReadMixin:
    """Parameterized read methods kept separate from lifecycle/write methods."""

    def _read_connection(self) -> sqlite3.Connection:
        """Return the concrete store's owned SQLite connection."""
        raise NotImplementedError

    def search(  # noqa: PLR0913
        self,
        query: str = "",
        *,
        category_id: str | None = None,
        updated_from: date | None = None,
        updated_to: date | None = None,
        limit: int = 10,
        offset: int = 0,
        posted_from: date | None = None,
        posted_to: date | None = None,
    ) -> tuple[StoredNotice, ...]:
        """Search active notices through FTS5 and bounded metadata filters."""
        return search_notices(
            self._read_connection(),
            query,
            category_id,
            updated_from,
            updated_to,
            limit,
            offset,
            posted_from,
            posted_to,
        )

    def search_count(
        self,
        query: str = "",
        *,
        category_id: str | None = None,
        posted_from: date | None = None,
        posted_to: date | None = None,
    ) -> int:
        """Count active search matches for exact pagination metadata."""
        return search_count(
            self._read_connection(), query, category_id, posted_from, posted_to
        )

    def latest(
        self, *, category_id: str | None = None, limit: int = 10, offset: int = 0
    ) -> tuple[StoredNotice, ...]:
        """Return active notices ordered by posted date and DUID."""
        return latest_notices(self._read_connection(), category_id, limit, offset)

    def latest_count(self, *, category_id: str | None = None) -> int:
        """Count active notices for a latest-results page."""
        return latest_count(self._read_connection(), category_id)

    def category_counts(self) -> tuple[tuple[str, int], ...]:
        """Return counts for stored, non-tombstoned notices by category ID."""
        return category_counts(self._read_connection())
