"""Canonical SQLite schema objects shared by storage consumers."""

from typing import Final

SCHEMA_VERSION: Final = 1
REQUIRED_TABLES: Final[frozenset[str]] = frozenset(
    {"schema_version", "notices", "notice_revisions", "crawl_runs", "notices_fts"}
)
REQUIRED_INDEXES: Final[frozenset[str]] = frozenset(
    {
        "notices_updated_idx",
        "notices_category_idx",
        "notices_tombstone_idx",
        "crawl_runs_status_idx",
    }
)


def _columns(*names: str) -> frozenset[str]:
    """Build one immutable column-name set."""
    return frozenset(names)


REQUIRED_COLUMNS: Final[tuple[tuple[str, frozenset[str]], ...]] = (
    ("schema_version", frozenset({"version"})),
    (
        "notices",
        _columns(
            "duid",
            "category_id",
            "category_name",
            "title",
            "posted_date",
            "updated_date",
            "department",
            "source_url",
            "body",
            "body_expires_at",
            "content_hash",
            "attachments_present",
            "collected_at",
            "tombstone_at",
            "source_status",
        ),
    ),
    (
        "notice_revisions",
        _columns(
            "revision_id",
            "duid",
            "content_hash",
            "category_id",
            "category_name",
            "title",
            "posted_date",
            "updated_date",
            "department",
            "source_url",
            "attachments_present",
            "collected_at",
            "changed_at",
        ),
    ),
    (
        "crawl_runs",
        _columns(
            "run_id",
            "status",
            "checkpoint_page",
            "pages_seen",
            "detail_requests",
            "index_requests",
            "retry_count",
            "block_reason",
            "started_at",
            "updated_at",
            "finished_at",
        ),
    ),
    ("notices_fts", _columns("duid", "title", "category_name", "department", "body")),
)
FTS5_SHADOW_TABLES: Final[frozenset[str]] = _columns(
    "notices_fts_config",
    "notices_fts_content",
    "notices_fts_data",
    "notices_fts_docsize",
    "notices_fts_idx",
)
