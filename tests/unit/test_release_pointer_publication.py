"""Executable fake-GitHub tests for immutable Release pointer publication."""

import json
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import final

import pytest

from kw_notice_mcp.release import (
    PublicationError,
    SnapshotError,
    SnapshotManifest,
    load_pointer,
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
    pointer_before: str
    new: SnapshotManifest


@dataclass(frozen=True, slots=True)
class _FailurePlan:
    """Fake API failure controls for one executable scenario."""

    fail_asset: str | None = None
    pointer_failure: str | None = None
    pointer_failure_body: str | None = None
    readback_override: str | None = None


@final
class _FakeGitHubRelease:
    """Fake Release resource with observable assets and one body pointer."""

    def __init__(
        self,
        release_dir: Path,
        *,
        initial_pointer: str | None,
        failure: _FailurePlan | None = None,
    ) -> None:
        plan = failure or _FailurePlan()
        self.release_dir = release_dir
        self.body = initial_pointer
        self.fail_asset = plan.fail_asset
        self.pointer_failure = plan.pointer_failure
        self.pointer_failure_body = plan.pointer_failure_body
        self.readback_override = plan.readback_override
        self.uploaded: list[str] = []
        self.verified: list[str] = []
        self.events: list[str] = []

    def upload_generation_asset(self, source: Path, asset_name: str) -> None:
        self.events.append(f"upload:{asset_name}")
        if asset_name == self.fail_asset:
            reason = "asset_upload_failure"
            raise PublicationError(reason)
        _ = shutil.copyfile(source, self.release_dir / asset_name)
        self.uploaded.append(asset_name)

    def verify_generation_assets(self, assets: tuple[tuple[Path, str], ...]) -> None:
        self.events.append("verify")
        for source, asset_name in assets:
            if (self.release_dir / asset_name).read_bytes() != source.read_bytes():
                reason = "asset_verification_failure"
                raise PublicationError(reason)
            self.verified.append(asset_name)

    def read_pointer(self) -> str | None:
        self.events.append("read-pointer")
        if self.readback_override is not None:
            return self.readback_override
        return self.body

    def update_pointer(self, pointer: str) -> None:
        self.events.append("update-pointer")
        if self.pointer_failure_body is not None:
            self.body = self.pointer_failure_body
        if self.pointer_failure is not None:
            reason = self.pointer_failure
            raise PublicationError(reason)
        self.body = pointer


def _scenario(tmp_path: Path) -> _Scenario:
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    old = prepare_snapshot(
        _database(tmp_path / "old.sqlite3", "old"), tmp_path / "old-stage"
    )
    for source in (old.database, old.checksum, old.pointer):
        _ = shutil.copyfile(source, release_dir / source.name)
    new = prepare_snapshot(
        _database(tmp_path / "new.sqlite3", "new"), tmp_path / "new-stage"
    )
    return _Scenario(
        release_dir=release_dir,
        pointer_before=(release_dir / old.pointer.name).read_text(encoding="utf-8"),
        new=new,
    )


def _new_pointer(scenario: _Scenario) -> str:
    manifest_asset = f"notices-manifest-{scenario.new.sha256}.json"
    return json.dumps(
        {"version": 1, "manifest_asset": manifest_asset}, separators=(",", ":")
    )


def test_success_uploads_verifies_and_reads_back_body_pointer(tmp_path: Path) -> None:
    """Given a valid generation, all assets verify before a successful body edit."""
    scenario = _scenario(tmp_path)
    fake = _FakeGitHubRelease(
        scenario.release_dir, initial_pointer=scenario.pointer_before
    )

    publish_generation(scenario.new, fake)

    manifest_asset = f"notices-manifest-{scenario.new.sha256}.json"
    assert fake.uploaded == [
        scenario.new.database.name,
        scenario.new.checksum.name,
        manifest_asset,
    ]
    assert fake.verified == fake.uploaded
    assert fake.events.index("verify") < fake.events.index("update-pointer")
    assert fake.events[-1] == "read-pointer"
    assert fake.body == _new_pointer(scenario)


def test_partial_upload_never_updates_authoritative_body(tmp_path: Path) -> None:
    """Given a partial upload, the prior body remains authoritative."""
    scenario = _scenario(tmp_path)
    fake = _FakeGitHubRelease(
        scenario.release_dir,
        initial_pointer=scenario.pointer_before,
        failure=_FailurePlan(fail_asset=scenario.new.checksum.name),
    )

    with pytest.raises(PublicationError, match="asset_upload_failure"):
        publish_generation(scenario.new, fake)

    assert fake.body == scenario.pointer_before
    assert fake.uploaded == [scenario.new.database.name]
    assert "update-pointer" not in fake.events


def test_pointer_edit_failure_reads_back_and_preserves_prior_pointer(
    tmp_path: Path,
) -> None:
    """Given a later pointer edit failure, read-back preserves the prior body."""
    scenario = _scenario(tmp_path)
    fake = _FakeGitHubRelease(
        scenario.release_dir,
        initial_pointer=scenario.pointer_before,
        failure=_FailurePlan(pointer_failure="pointer_edit_failure"),
    )

    with pytest.raises(PublicationError, match="pointer_edit_failure"):
        publish_generation(scenario.new, fake)

    assert fake.body == scenario.pointer_before
    assert fake.events[-1] == "read-pointer"


def test_ambiguous_api_failure_is_success_when_readback_is_new_pointer(
    tmp_path: Path,
) -> None:
    """Given an ambiguous API error, matching new-body read-back proves success."""
    scenario = _scenario(tmp_path)
    fake = _FakeGitHubRelease(
        scenario.release_dir,
        initial_pointer=scenario.pointer_before,
        failure=_FailurePlan(
            pointer_failure="ambiguous_api_failure",
            pointer_failure_body=_new_pointer(scenario),
        ),
    )

    publish_generation(scenario.new, fake)

    assert fake.body == _new_pointer(scenario)


def test_post_edit_readback_mismatch_is_not_reported_as_success(
    tmp_path: Path,
) -> None:
    """Given a stale post-edit read-back, publication fails closed."""
    scenario = _scenario(tmp_path)
    fake = _FakeGitHubRelease(
        scenario.release_dir,
        initial_pointer=scenario.pointer_before,
        failure=_FailurePlan(readback_override=scenario.pointer_before),
    )

    with pytest.raises(PublicationError, match="pointer_readback_mismatch"):
        publish_generation(scenario.new, fake)


def test_initial_pointer_failure_leaves_no_authoritative_generation(
    tmp_path: Path,
) -> None:
    """Given no prior body, an edit failure leaves no authoritative generation."""
    scenario = _scenario(tmp_path)
    fake = _FakeGitHubRelease(
        scenario.release_dir,
        initial_pointer=None,
        failure=_FailurePlan(pointer_failure="initial_pointer_failure"),
    )

    with pytest.raises(PublicationError, match="initial_pointer_failure"):
        publish_generation(scenario.new, fake)

    assert fake.body is None
    assert fake.events[-1] == "read-pointer"


def test_pointer_parser_rejects_mutable_asset_and_extra_fields(tmp_path: Path) -> None:
    """Given an untrusted body, strict parsing rejects mutable or extra fields."""
    pointer = tmp_path / "pointer.json"
    _ = pointer.write_text(
        json.dumps(
            {
                "version": 1,
                "manifest_asset": "notices-manifest.json",
                "extra": "unexpected",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SnapshotError, match="pointer"):
        _ = load_pointer(pointer)
