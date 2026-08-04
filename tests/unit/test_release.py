"""Release snapshot decisions and local artifact preparation."""

import hashlib
import sqlite3
from pathlib import Path

import pytest

from kw_notice_mcp.release import (
    RestoreAction,
    SnapshotError,
    prepare_snapshot,
    publication_decision,
    restore_decision,
)
from kw_notice_mcp.storage import initialize_database


def test_only_successful_crawl_can_publish() -> None:
    """Given CLI exit codes, only success selects Release publication."""
    assert publication_decision(0).publish is True
    for exit_code in (10, 11, 12, 13, 1):
        assert publication_decision(exit_code).publish is False


def test_restore_decision_distinguishes_missing_from_download_failure() -> None:
    """Given release state, missing assets initialize but download failures retain."""
    assert (
        restore_decision(release_exists=False, download_succeeded=False)
        is RestoreAction.INITIALIZE
    )
    assert (
        restore_decision(release_exists=True, download_succeeded=True)
        is RestoreAction.RESTORE
    )
    assert (
        restore_decision(release_exists=True, download_succeeded=False)
        is RestoreAction.RETAIN
    )


def test_prepare_snapshot_copies_valid_sanitized_db_and_checksum(
    tmp_path: Path,
) -> None:
    """Given a metadata DB, commit one checksum-matched immutable generation."""
    source = _database(tmp_path / "source.sqlite3")
    output = tmp_path / "release"

    manifest = prepare_snapshot(source, output)

    assert manifest.generation_dir.parent == output / "generations"
    assert manifest.generation_dir.name == manifest.sha256
    assert manifest.database.read_bytes() == source.read_bytes()
    expected = hashlib.sha256(manifest.database.read_bytes()).hexdigest()
    assert manifest.sha256 == expected
    assert manifest.checksum.read_text(encoding="utf-8") == (
        f"{expected}  {manifest.database.name}\n"
    )
    assert manifest.pointer.is_file()


def test_prepare_snapshot_rejects_retained_body_text(tmp_path: Path) -> None:
    """Given a DB containing body text, refuse to create Release inputs."""
    source = _database(tmp_path / "source.sqlite3", body="retained text")

    with pytest.raises(SnapshotError):
        _ = prepare_snapshot(source, tmp_path / "release")


def _database(path: Path, *, body: str | None = None) -> Path:
    """Create one canonical metadata database for release tests."""
    initialize_database(path)
    connection = sqlite3.connect(path)
    _ = connection.execute(
        """
        INSERT INTO notices(
            duid, category_id, category_name, title, posted_date, updated_date,
            department, source_url, body, body_expires_at, content_hash,
            attachments_present, collected_at, tombstone_at, source_status
        ) VALUES ('1001', 'general', 'General', 'Fixture', '2026-08-01',
                  '2026-08-01', 'Department', 'https://example.test/1001',
                  ?, NULL, 'hash', 0, '2026-08-01T00:00:00+00:00', NULL, 'active')
        """,
        (body,),
    )
    _ = connection.execute(
        """
        INSERT INTO notices_fts(duid, title, category_name, department, body)
        VALUES ('1001', 'Fixture', 'General', 'Department', '')
        """
    )
    connection.commit()
    connection.close()
    return path
