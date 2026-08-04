"""Actual helper CLI coverage for workflow restore and consumer installation."""

import shutil
import sqlite3
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

from kw_notice_mcp.release import ReleaseManifest
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
    connection.commit()
    connection.close()
    return path


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "kw_notice_mcp.release", *arguments],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )


def test_prepare_verify_and_restore_cli_rejects_later_checksum_mismatch(
    tmp_path: Path,
) -> None:
    """Given actual helper commands, mismatch cannot replace an accepted DB."""
    source = _database(tmp_path / "source.sqlite3")
    prepared = _run(
        "prepare",
        "--database",
        str(source),
        "--output-dir",
        str(tmp_path / "stage"),
    )
    assert prepared.returncode == 0
    pointer = Path(prepared.stdout.strip())
    verified = _run("verify-manifest", "--manifest", str(pointer))
    assert verified.returncode == 0

    destination = tmp_path / "consumer.sqlite3"
    restored = _run(
        "restore",
        "--manifest",
        str(pointer),
        "--assets-dir",
        str(pointer.parent),
        "--database",
        str(destination),
    )
    assert restored.returncode == 0
    accepted = destination.read_bytes()

    checksum = next(pointer.parent.glob("*.sha256"))
    _ = checksum.write_text(f"{'0' * 64}  invalid.sqlite3\n", encoding="ascii")
    rejected = _run(
        "restore",
        "--manifest",
        str(pointer),
        "--assets-dir",
        str(pointer.parent),
        "--database",
        str(destination),
    )

    assert rejected.returncode == 13
    assert destination.read_bytes() == accepted


def test_restore_cli_rejects_checksum_matched_database_with_body(
    tmp_path: Path,
) -> None:
    """Given an unsafe matched pair, the actual refresh restore CLI retains DB."""
    source = _database(tmp_path / "unsafe.sqlite3")
    connection = sqlite3.connect(source)
    _ = connection.execute("UPDATE notices SET body = 'retained'")
    connection.commit()
    connection.close()
    digest = sha256(source.read_bytes()).hexdigest()
    database_asset = f"notices-{digest}.sqlite3"
    checksum_asset = f"{database_asset}.sha256"
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    _ = shutil.copyfile(source, release_dir / database_asset)
    _ = (release_dir / checksum_asset).write_text(
        f"{digest}  {database_asset}\n", encoding="ascii"
    )
    manifest = ReleaseManifest(
        sha256=digest,
        database_asset=database_asset,
        checksum_asset=checksum_asset,
    )
    pointer = release_dir / "notices-manifest.json"
    _ = pointer.write_text(f"{manifest.model_dump_json()}\n", encoding="utf-8")
    pointer_before = pointer.read_bytes()
    destination = _database(tmp_path / "consumer.sqlite3")
    consumer_before = destination.read_bytes()

    rejected = _run(
        "restore",
        "--manifest",
        str(pointer),
        "--assets-dir",
        str(release_dir),
        "--database",
        str(destination),
    )

    assert rejected.returncode == 13
    assert destination.read_bytes() == consumer_before
    assert pointer.read_bytes() == pointer_before
    assert not tuple(tmp_path.glob(".kw-notice-install-*"))


def test_restore_cli_rejects_checksum_matched_database_with_fts_body(
    tmp_path: Path,
) -> None:
    """Given only the FTS projection has body text, restore retains the DB."""
    source = _database(tmp_path / "unsafe.sqlite3")
    connection = sqlite3.connect(source)
    _ = connection.execute(
        "UPDATE notices_fts SET body = 'retained FTS body' WHERE duid = '1001'"
    )
    connection.commit()
    connection.close()
    digest = sha256(source.read_bytes()).hexdigest()
    database_asset = f"notices-{digest}.sqlite3"
    checksum_asset = f"{database_asset}.sha256"
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    _ = shutil.copyfile(source, release_dir / database_asset)
    _ = (release_dir / checksum_asset).write_text(
        f"{digest}  {database_asset}\n", encoding="ascii"
    )
    manifest = ReleaseManifest(
        sha256=digest,
        database_asset=database_asset,
        checksum_asset=checksum_asset,
    )
    pointer = release_dir / "notices-manifest.json"
    _ = pointer.write_text(f"{manifest.model_dump_json()}\n", encoding="utf-8")
    pointer_before = pointer.read_bytes()
    destination = _database(tmp_path / "consumer.sqlite3")
    consumer_before = destination.read_bytes()

    rejected = _run(
        "restore",
        "--manifest",
        str(pointer),
        "--assets-dir",
        str(release_dir),
        "--database",
        str(destination),
    )

    assert rejected.returncode == 13
    assert destination.read_bytes() == consumer_before
    assert pointer.read_bytes() == pointer_before
    assert not tuple(tmp_path.glob(".kw-notice-install-*"))
