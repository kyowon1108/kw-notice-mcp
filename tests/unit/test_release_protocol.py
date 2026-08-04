"""Failure-safe Release generation and consumer protocol tests."""

import shutil
import sqlite3
from hashlib import sha256
from pathlib import Path

import pytest

from kw_notice_mcp import release_snapshot
from kw_notice_mcp.release import (
    RestoreObservation,
    SnapshotError,
    execute_refresh,
    install_release,
    load_manifest,
    prepare_snapshot,
    refresh_decision,
    verify_pair,
)
from kw_notice_mcp.storage import initialize_database


def _database(path: Path, marker: str) -> Path:
    initialize_database(path)
    connection = sqlite3.connect(path)
    _ = connection.execute(
        """
        INSERT INTO crawl_runs(
            run_id, status, checkpoint_page, pages_seen, detail_requests,
            index_requests, retry_count, block_reason, started_at, updated_at,
            finished_at
        ) VALUES (?, 'success', 0, 0, 0, 0, 0, NULL,
                  '2026-08-01T00:00:00+00:00', '2026-08-01T00:00:00+00:00',
                  '2026-08-01T00:00:00+00:00')
        """,
        (marker,),
    )
    connection.commit()
    connection.close()
    return path


def _publish_complete(snapshot_dir: Path, release_dir: Path) -> Path:
    manifest_path = snapshot_dir / "notices-manifest.json"
    manifest = load_manifest(manifest_path)
    _ = release_dir.mkdir(parents=True, exist_ok=True)
    for name in (manifest.database_asset, manifest.checksum_asset):
        _ = shutil.copyfile(snapshot_dir / name, release_dir / name)
    return Path(shutil.copyfile(manifest_path, release_dir / manifest_path.name))


def test_checksum_mismatch_does_not_replace_consumer_database(tmp_path: Path) -> None:
    """Given a mismatched pair, installation fails before replacing the consumer DB."""
    snapshot = prepare_snapshot(
        _database(tmp_path / "source.sqlite3", "new"), tmp_path / "stage"
    )
    release_dir = tmp_path / "release"
    pointer = _publish_complete(snapshot.generation_dir, release_dir)
    manifest = load_manifest(pointer)
    _ = (release_dir / manifest.checksum_asset).write_text(
        f"{'0' * 64}  {manifest.database_asset}\n", encoding="ascii"
    )
    consumer = _database(tmp_path / "consumer.sqlite3", "old")
    before = consumer.read_bytes()

    with pytest.raises(SnapshotError):
        _ = install_release(pointer, release_dir, consumer)

    assert consumer.read_bytes() == before


def test_incomplete_asset_pair_does_not_replace_consumer_database(
    tmp_path: Path,
) -> None:
    """Given a missing generation component, installation retains the local DB."""
    snapshot = prepare_snapshot(
        _database(tmp_path / "source.sqlite3", "new"), tmp_path / "stage"
    )
    release_dir = tmp_path / "release"
    pointer = _publish_complete(snapshot.generation_dir, release_dir)
    manifest = load_manifest(pointer)
    (release_dir / manifest.checksum_asset).unlink()
    consumer = _database(tmp_path / "consumer.sqlite3", "old")
    before = consumer.read_bytes()

    with pytest.raises(SnapshotError):
        _ = install_release(pointer, release_dir, consumer)

    assert consumer.read_bytes() == before


@pytest.mark.parametrize(
    "observation",
    [
        RestoreObservation.API_FAILURE,
        RestoreObservation.DOWNLOAD_FAILURE,
        RestoreObservation.CHECKSUM_MISMATCH,
        RestoreObservation.INCOMPLETE,
    ],
)
def test_remote_restore_failures_disable_crawl_and_publication(
    observation: RestoreObservation, tmp_path: Path
) -> None:
    """Given a remote failure, the refresh gate retains and stops before crawl."""
    consumer = _database(tmp_path / "consumer.sqlite3", "old")
    before = consumer.read_bytes()

    calls: list[str] = []

    class Effects:
        def initialize(self) -> None:
            calls.append("initialize")

        def restore(self) -> None:
            _ = consumer.write_bytes(b"replacement")
            calls.append("restore")

        def crawl(self) -> int:
            calls.append("crawl")
            return 0

        def publish(self) -> None:
            calls.append("publish")

    decision = refresh_decision(observation)
    execute_refresh(observation, Effects())

    assert decision.crawl is False
    assert decision.publish is False
    assert calls == []
    assert consumer.read_bytes() == before


def test_local_pair_staging_failure_exposes_no_new_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Given checksum-write failure, directory commit never exposes the new DB."""
    stage = tmp_path / "stage"
    previous = prepare_snapshot(_database(tmp_path / "old.sqlite3", "old"), stage)
    pointers_before = tuple(stage.rglob("notices-manifest.json"))
    before = tuple(sorted(path.name for path in (stage / "generations").iterdir()))
    new_database = _database(tmp_path / "new.sqlite3", "new")
    new_digest = sha256(new_database.read_bytes()).hexdigest()

    def fail_checksum(_path: Path, _content: str) -> None:
        raise OSError

    monkeypatch.setattr(release_snapshot, "_write_checksum", fail_checksum)

    with pytest.raises(SnapshotError):
        _ = prepare_snapshot(new_database, stage)

    after = tuple(sorted(path.name for path in (stage / "generations").iterdir()))
    assert after == before == (previous.sha256,)
    assert (
        tuple(stage.rglob("notices-manifest.json"))
        == pointers_before
        == (previous.pointer,)
    )
    assert not (stage / "generations" / new_digest).exists()
    assert not tuple(stage.glob(".staging-*"))
    manifest = load_manifest(previous.pointer)
    assert (
        verify_pair(
            previous.generation_dir / manifest.database_asset,
            previous.generation_dir / manifest.checksum_asset,
            expected_sha256=manifest.sha256,
        )
        is None
    )


def test_partial_publication_cannot_move_consumer_to_new_generation(
    tmp_path: Path,
) -> None:
    """Given only a new DB asset, the old pointer remains the accepted generation."""
    release_dir = tmp_path / "release"
    old = prepare_snapshot(
        _database(tmp_path / "old.sqlite3", "old"), tmp_path / "old-stage"
    )
    pointer = _publish_complete(old.generation_dir, release_dir)
    consumer = tmp_path / "consumer.sqlite3"
    _ = install_release(pointer, release_dir, consumer)
    accepted = consumer.read_bytes()

    new = prepare_snapshot(
        _database(tmp_path / "new.sqlite3", "new"), tmp_path / "new-stage"
    )
    new_manifest = load_manifest(new.pointer)
    _ = shutil.copyfile(
        new.generation_dir / new_manifest.database_asset,
        release_dir / new_manifest.database_asset,
    )

    _ = install_release(pointer, release_dir, consumer)

    assert consumer.read_bytes() == accepted
