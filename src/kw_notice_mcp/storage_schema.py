"""Versioned SQLite schema and FTS5 capability checks."""

import sqlite3
from typing import Final

from kw_notice_mcp.storage_errors import (
    FTS5UnavailableError,
    SchemaMigrationError,
    unavailable_storage,
)
from kw_notice_mcp.storage_schema_contract import (
    FTS5_SHADOW_TABLES,
    REQUIRED_COLUMNS,
    REQUIRED_INDEXES,
    REQUIRED_TABLES,
    SCHEMA_VERSION,
)
from kw_notice_mcp.storage_support import fetch_all, fetch_one

_SCHEMA_SQL: Final = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS notices (
    duid TEXT PRIMARY KEY,
    category_id TEXT NOT NULL,
    category_name TEXT NOT NULL,
    title TEXT NOT NULL,
    posted_date TEXT NOT NULL,
    updated_date TEXT NOT NULL,
    department TEXT NOT NULL,
    source_url TEXT NOT NULL,
    body TEXT,
    body_expires_at TEXT,
    content_hash TEXT NOT NULL,
    attachments_present INTEGER NOT NULL CHECK (attachments_present IN (0, 1)),
    collected_at TEXT NOT NULL,
    tombstone_at TEXT,
    source_status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notice_revisions (
    revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    duid TEXT NOT NULL REFERENCES notices(duid),
    content_hash TEXT NOT NULL,
    category_id TEXT NOT NULL,
    category_name TEXT NOT NULL,
    title TEXT NOT NULL,
    posted_date TEXT NOT NULL,
    updated_date TEXT NOT NULL,
    department TEXT NOT NULL,
    source_url TEXT NOT NULL,
    attachments_present INTEGER NOT NULL CHECK (attachments_present IN (0, 1)),
    collected_at TEXT NOT NULL,
    changed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS crawl_runs (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    checkpoint_page INTEGER NOT NULL DEFAULT 0,
    pages_seen INTEGER NOT NULL DEFAULT 0,
    detail_requests INTEGER NOT NULL DEFAULT 0,
    index_requests INTEGER NOT NULL DEFAULT 0,
    retry_count INTEGER NOT NULL DEFAULT 0,
    block_reason TEXT,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS notices_fts USING fts5(
    duid UNINDEXED,
    title,
    category_name,
    department,
    body
);
CREATE INDEX IF NOT EXISTS notices_updated_idx ON notices(updated_date DESC);
CREATE INDEX IF NOT EXISTS notices_category_idx ON notices(category_id);
CREATE INDEX IF NOT EXISTS notices_tombstone_idx ON notices(tombstone_at);
CREATE INDEX IF NOT EXISTS crawl_runs_status_idx ON crawl_runs(status);
"""

_EXPECTED_INTERNAL_TABLES: Final[frozenset[str]] = frozenset({"sqlite_sequence"})
_EXPECTED_AUTO_INDEXES: Final[frozenset[str]] = frozenset(
    {"sqlite_autoindex_crawl_runs_1", "sqlite_autoindex_notices_1"}
)
_EXPECTED_TRIGGERS: Final[frozenset[str]] = frozenset()
_EXPECTED_FTS_COLUMNS: Final[tuple[tuple[str, frozenset[str]], ...]] = (
    ("notices_fts_config", frozenset({"k", "v"})),
    ("notices_fts_content", frozenset({"id", "c0", "c1", "c2", "c3", "c4"})),
    ("notices_fts_data", frozenset({"id", "block"})),
    ("notices_fts_docsize", frozenset({"id", "sz"})),
    ("notices_fts_idx", frozenset({"segid", "term", "pgno"})),
)
_EXPECTED_INTERNAL_COLUMNS: Final[tuple[tuple[str, frozenset[str]], ...]] = (
    ("sqlite_sequence", frozenset({"name", "seq"})),
)


def detect_fts5(connection: sqlite3.Connection) -> bool:
    """Probe the connection for the SQLite FTS5 virtual-table module."""
    try:
        row = fetch_one(
            connection,
            "SELECT 1 FROM pragma_module_list WHERE name = ?",
            ("fts5",),
        )
    except sqlite3.OperationalError as error:
        if "locked" in str(error).lower():
            raise
        raise FTS5UnavailableError from error
    return row is not None


def require_fts5(connection: sqlite3.Connection) -> None:
    """Raise a typed setup error when FTS5 is not available."""
    if not detect_fts5(connection):
        raise FTS5UnavailableError


def migrate(connection: sqlite3.Connection) -> None:
    """Create or validate schema version one inside the caller's transaction."""
    user_version = fetch_one(connection, "PRAGMA user_version")
    if user_version is not None:
        value = user_version[0]
        if isinstance(value, int) and value > SCHEMA_VERSION:
            raise SchemaMigrationError(value)
    try:
        existing = fetch_one(
            connection, "SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1"
        )
    except sqlite3.OperationalError:
        existing = None
    version = existing[0] if existing is not None else None
    if version is not None and not isinstance(version, int):
        reason = "schema version"
        raise unavailable_storage(reason)
    if version is not None and version > SCHEMA_VERSION:
        raise SchemaMigrationError(version)
    _ = connection.executescript(_SCHEMA_SQL)
    if version is None:
        _ = connection.execute(
            "INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,)
        )
    elif version < SCHEMA_VERSION:
        _ = connection.execute(
            "UPDATE schema_version SET version = ?", (SCHEMA_VERSION,)
        )
    _ = connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def table_names(connection: sqlite3.Connection) -> frozenset[str]:
    """Return the schema objects visible on a connection."""
    rows = fetch_all(
        connection,
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')",
    )
    return frozenset(str(row[0]) for row in rows)


def schema_is_valid(connection: sqlite3.Connection) -> bool:
    """Check the complete schema contract required by read-only consumers."""
    object_rows = fetch_all(
        connection,
        "SELECT name, type FROM sqlite_master WHERE type IN (?, ?, ?)",
        ("table", "virtual table", "view"),
    )
    object_types = {str(row[0]): str(row[1]) for row in object_rows}
    expected_object_types = {
        **{table: "table" for table in REQUIRED_TABLES if table != "notices_fts"},
        "notices_fts": "table",
        **dict.fromkeys(FTS5_SHADOW_TABLES, "table"),
        **dict.fromkeys(_EXPECTED_INTERNAL_TABLES, "table"),
    }
    names = frozenset(object_types)
    indexes = frozenset(
        str(row[0])
        for row in fetch_all(
            connection, "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
    )
    columns_valid = all(
        expected_columns
        == (
            frozenset(
                str(row[0])
                for row in fetch_all(
                    connection,
                    "SELECT name FROM pragma_table_info(?)",
                    (table,),
                )
            )
        )
        for table, expected_columns in (
            *REQUIRED_COLUMNS,
            *_EXPECTED_FTS_COLUMNS,
            *_EXPECTED_INTERNAL_COLUMNS,
        )
    )
    versions_valid = fetch_one(connection, "PRAGMA user_version") == (
        SCHEMA_VERSION,
    ) and fetch_one(
        connection,
        "SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1",
    ) == (SCHEMA_VERSION,)
    fts_sql = fetch_one(
        connection,
        "SELECT sql FROM sqlite_master WHERE name = ?",
        ("notices_fts",),
    )
    fts_sql_value = fts_sql[0] if fts_sql is not None else None
    fts_structure_valid = (
        isinstance(fts_sql_value, str)
        and "USING FTS5" in fts_sql_value.upper()
        and names >= FTS5_SHADOW_TABLES
    )
    triggers = frozenset(
        str(row[0])
        for row in fetch_all(
            connection, "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        )
    )
    fts_query_valid = True
    try:
        _ = fetch_one(
            connection,
            "SELECT COUNT(*) FROM notices_fts WHERE notices_fts MATCH ?",
            ("contract_probe",),
        )
    except sqlite3.Error:
        fts_query_valid = False
    return (
        object_types == expected_object_types
        and indexes == REQUIRED_INDEXES | _EXPECTED_AUTO_INDEXES
        and triggers == _EXPECTED_TRIGGERS
        and columns_valid
        and versions_valid
        and fts_structure_valid
        and fetch_one(connection, "PRAGMA integrity_check") == ("ok",)
        and fts_query_valid
    )
