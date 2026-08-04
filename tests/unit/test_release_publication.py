"""Executable, network-free Release publication failure simulations."""

import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import final

import pytest

from kw_notice_mcp.release import (
    PublicationError,
    SnapshotManifest,
    install_release,
    prepare_snapshot,
    publish_generation,
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


@dataclass(frozen=True, slots=True)
class _Scenario:
    release_dir: Path
    consumer: Path
    accepted: bytes
    pointer_before: bytes
    new: SnapshotManifest


@final
class _FakePublicationAdapter:
    """In-memory-control adapter backed only by temporary local paths."""

    def __init__(
        self,
        release_dir: Path,
        *,
        fail_asset: str | None = None,
        fail_api: bool = False,
        fail_manifest: bool = False,
    ) -> None:
        self.release_dir = release_dir
        self.fail_asset = fail_asset
        self.fail_api = fail_api
        self.fail_manifest = fail_manifest
        self.uploaded: list[str] = []

    def upload_generation_asset(self, source: Path, asset_name: str) -> None:
        if self.fail_api:
            reason = "api_failure"
            raise PublicationError(reason)
        if self.fail_asset == asset_name:
            reason = "asset_upload_failure"
            raise PublicationError(reason)
        _ = shutil.copyfile(source, self.release_dir / asset_name)
        self.uploaded.append(asset_name)

    def verify_generation_assets(self, assets: tuple[tuple[Path, str], ...]) -> None:
        for source, asset_name in assets:
            if (self.release_dir / asset_name).read_bytes() != source.read_bytes():
                reason = "asset_verification_failure"
                raise PublicationError(reason)

    def read_pointer(self) -> str | None:
        return (self.release_dir / "notices-manifest.json").read_text(encoding="utf-8")

    def update_pointer(self, pointer: str) -> None:
        if self.fail_manifest:
            reason = "manifest_upload_failure"
            raise PublicationError(reason)
        _ = (self.release_dir / "release-body.json").write_text(
            pointer, encoding="utf-8"
        )

    def replace_manifest(self, source: Path) -> None:
        if self.fail_manifest:
            reason = "manifest_upload_failure"
            raise PublicationError(reason)
        _ = shutil.copyfile(source, self.release_dir / source.name)


def _scenario(tmp_path: Path) -> _Scenario:
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    old = prepare_snapshot(
        _database(tmp_path / "old.sqlite3", "old"), tmp_path / "old-stage"
    )
    for source in (old.database, old.checksum, old.pointer):
        _ = shutil.copyfile(source, release_dir / source.name)

    consumer = tmp_path / "consumer.sqlite3"
    _ = install_release(release_dir / old.pointer.name, release_dir, consumer)
    new = prepare_snapshot(
        _database(tmp_path / "new.sqlite3", "new"), tmp_path / "new-stage"
    )
    return _Scenario(
        release_dir=release_dir,
        consumer=consumer,
        accepted=consumer.read_bytes(),
        pointer_before=(release_dir / old.pointer.name).read_bytes(),
        new=new,
    )


def _assert_prior_generation_remains_authoritative(scenario: _Scenario) -> None:
    pointer = scenario.release_dir / "notices-manifest.json"
    assert pointer.read_bytes() == scenario.pointer_before
    _ = install_release(pointer, scenario.release_dir, scenario.consumer)
    assert scenario.consumer.read_bytes() == scenario.accepted


def test_checksum_asset_upload_failure_never_advances_consumer(tmp_path: Path) -> None:
    scenario = _scenario(tmp_path)
    adapter = _FakePublicationAdapter(
        scenario.release_dir, fail_asset=scenario.new.checksum.name
    )

    with pytest.raises(PublicationError, match="asset_upload_failure"):
        publish_generation(scenario.new, adapter)

    assert adapter.uploaded == [scenario.new.database.name]
    assert (scenario.release_dir / scenario.new.database.name).is_file()
    assert not (scenario.release_dir / scenario.new.checksum.name).exists()
    _assert_prior_generation_remains_authoritative(scenario)


def test_manifest_upload_failure_leaves_complete_new_generation_unadopted(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    adapter = _FakePublicationAdapter(scenario.release_dir, fail_manifest=True)

    with pytest.raises(PublicationError, match="manifest_upload_failure"):
        publish_generation(scenario.new, adapter)

    assert adapter.uploaded == [
        scenario.new.database.name,
        scenario.new.checksum.name,
        f"notices-manifest-{scenario.new.sha256}.json",
    ]
    assert (scenario.release_dir / scenario.new.database.name).is_file()
    assert (scenario.release_dir / scenario.new.checksum.name).is_file()
    _assert_prior_generation_remains_authoritative(scenario)


def test_publication_api_failure_uploads_nothing_and_keeps_consumer(
    tmp_path: Path,
) -> None:
    scenario = _scenario(tmp_path)
    adapter = _FakePublicationAdapter(scenario.release_dir, fail_api=True)

    with pytest.raises(PublicationError, match="api_failure"):
        publish_generation(scenario.new, adapter)

    assert adapter.uploaded == []
    assert not (scenario.release_dir / scenario.new.database.name).exists()
    assert not (scenario.release_dir / scenario.new.checksum.name).exists()
    _assert_prior_generation_remains_authoritative(scenario)
