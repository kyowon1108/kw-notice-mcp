"""Atomic FTS projection helpers for body-retention transitions."""

import sqlite3

from kw_notice_mcp.storage_support import fetch_one
from kw_notice_mcp.values import DUID


def sync_fts(connection: sqlite3.Connection, duid: DUID) -> None:
    """Rebuild one notice's FTS row without retaining a body token when NULL."""
    _ = connection.execute("DELETE FROM notices_fts WHERE duid = ?", (duid,))
    row = fetch_one(
        connection,
        """
        SELECT duid, category_name, title, department, body, tombstone_at
        FROM notices WHERE duid = ?
        """,
        (duid,),
    )
    if row is not None and row[5] is None:
        _ = connection.execute(
            """
            INSERT INTO notices_fts(duid, title, category_name, department, body)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                row[0],
                row[2],
                row[1],
                row[3],
                row[4] if isinstance(row[4], str) else "",
            ),
        )
