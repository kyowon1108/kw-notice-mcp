"""Adversarial validation at the Release consumer replacement boundary."""

import shutil
import sqlite3
from hashlib import sha256
from pathlib import Path

import pytest

from kw_notice_mcp.release import ReleaseManifest, SnapshotError, install_release
from kw_notice_mcp.storage import initialize_database


def _database(path: Path, *, body: str | None = None) -> Path:
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
    _ = connection.execute("PRAGMA user_version = 1")
    connection.commit()
    connection.close()
    return path


def _invalid_contract_database(path: Path, mutation: str) -> Path:
    _ = _database(path)
    connection = sqlite3.connect(path)
    _ = connection.executescript(mutation)
    connection.commit()
    connection.close()
    return path


def _release_pair(database: Path, release_dir: Path) -> Path:
    release_dir.mkdir()
    digest = sha256(database.read_bytes()).hexdigest()
    database_name = f"notices-{digest}.sqlite3"
    checksum_name = f"{database_name}.sha256"
    _ = shutil.copyfile(database, release_dir / database_name)
    _ = (release_dir / checksum_name).write_text(
        f"{digest}  {database_name}\n", encoding="ascii"
    )
    manifest = ReleaseManifest(
        sha256=digest,
        database_asset=database_name,
        checksum_asset=checksum_name,
    )
    pointer = release_dir / "notices-manifest.json"
    _ = pointer.write_text(f"{manifest.model_dump_json(indent=2)}\n", encoding="utf-8")
    return pointer


def _assert_rejected_without_replacement(
    pointer: Path,
    release_dir: Path,
    consumer: Path,
) -> None:
    consumer_before = consumer.read_bytes()
    pointer_before = pointer.read_bytes()

    with pytest.raises(SnapshotError):
        _ = install_release(pointer, release_dir, consumer)

    assert consumer.read_bytes() == consumer_before
    assert pointer.read_bytes() == pointer_before
    assert not tuple(consumer.parent.glob(".kw-notice-install-*"))


def test_checksum_matched_database_with_retained_body_is_rejected(
    tmp_path: Path,
) -> None:
    release_dir = tmp_path / "release"
    pointer = _release_pair(
        _database(tmp_path / "unsafe.sqlite3", body="retained"), release_dir
    )
    consumer = _database(tmp_path / "consumer.sqlite3")

    _assert_rejected_without_replacement(pointer, release_dir, consumer)


@pytest.mark.parametrize(
    "mutation",
    [
        "DROP TABLE notice_revisions",
        "ALTER TABLE notices DROP COLUMN category_name",
        "UPDATE schema_version SET version = 2; PRAGMA user_version = 2",
        (
            "DROP TABLE notices_fts; CREATE VIRTUAL TABLE notices_fts USING fts5("
            "duid, title, category_name, department)"
        ),
        (
            "DROP TABLE notices_fts; CREATE TABLE notices_fts("
            "duid TEXT, title TEXT, category_name TEXT, department TEXT, body TEXT)"
        ),
        "CREATE TABLE extra_pii(value TEXT)",
        "ALTER TABLE notices ADD COLUMN extra_pii TEXT",
        "CREATE INDEX extra_notices_idx ON notices(title)",
        (
            "CREATE TRIGGER extra_notice_trigger AFTER INSERT ON notices "
            "BEGIN SELECT 1; END"
        ),
        "CREATE TABLE notices_fts_extra(value TEXT)",
    ],
    ids=[
        "missing_table",
        "missing_column",
        "wrong_version",
        "missing_fts_column",
        "non_fts_table",
        "extra_table",
        "extra_column",
        "extra_index",
        "extra_trigger",
        "extra_fts_shadow",
    ],
)
def test_checksum_matched_database_with_invalid_complete_schema_is_rejected(
    mutation: str, tmp_path: Path
) -> None:
    source = _invalid_contract_database(tmp_path / "unsafe.sqlite3", mutation)
    release_dir = tmp_path / "release"
    pointer = _release_pair(source, release_dir)
    consumer = _database(tmp_path / "consumer.sqlite3")

    _assert_rejected_without_replacement(pointer, release_dir, consumer)


def test_checksum_matched_database_with_fts_body_is_rejected_when_notice_body_is_null(
    tmp_path: Path,
) -> None:
    source = _database(tmp_path / "unsafe.sqlite3")
    connection = sqlite3.connect(source)
    _ = connection.execute(
        "UPDATE notices_fts SET body = 'retained FTS body' WHERE duid = '1001'"
    )
    connection.commit()
    connection.close()
    release_dir = tmp_path / "release"
    pointer = _release_pair(source, release_dir)
    consumer = _database(tmp_path / "consumer.sqlite3")

    _assert_rejected_without_replacement(pointer, release_dir, consumer)


def test_checksum_matched_corrupt_sqlite_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "unsafe.sqlite3"
    _ = source.write_bytes(b"checksum-matched but not sqlite")
    release_dir = tmp_path / "release"
    pointer = _release_pair(source, release_dir)
    consumer = _database(tmp_path / "consumer.sqlite3")

    _assert_rejected_without_replacement(pointer, release_dir, consumer)


def test_checksum_matched_valid_database_replaces_consumer(tmp_path: Path) -> None:
    source = _database(tmp_path / "valid.sqlite3")
    release_dir = tmp_path / "release"
    pointer = _release_pair(source, release_dir)
    pointer_before = pointer.read_bytes()
    consumer = _database(tmp_path / "consumer.sqlite3")

    digest = install_release(pointer, release_dir, consumer)

    assert digest == sha256(source.read_bytes()).hexdigest()
    assert consumer.read_bytes() == source.read_bytes()
    assert pointer.read_bytes() == pointer_before
    assert not tuple(consumer.parent.glob(".kw-notice-install-*"))
