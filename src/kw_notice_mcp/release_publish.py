"""Failure-safe publication of immutable Release generations."""

from pathlib import Path
from typing import Protocol, final, override

from kw_notice_mcp.release_models import ReleasePointer, SnapshotManifest
from kw_notice_mcp.release_snapshot import immutable_manifest_asset_name

type PublicationAsset = tuple[Path, str]


@final
class PublicationError(Exception):
    """One publication operation failed before a new pointer was authoritative."""

    reason: str

    def __init__(self, reason: str) -> None:
        """Store only a bounded publication failure reason."""
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        return f"release publication failed: {self.reason}"


class PublicationAdapter(Protocol):
    """Side effects required to publish one immutable generation."""

    def upload_generation_asset(self, source: Path, asset_name: str) -> None:
        """Upload one immutable, content-addressed generation component."""
        ...

    def verify_generation_assets(self, assets: tuple[PublicationAsset, ...]) -> None:
        """Verify every uploaded generation component before pointer publication."""
        ...

    def read_pointer(self) -> str | None:
        """Read the one authoritative Release body pointer."""
        ...

    def update_pointer(self, pointer: str) -> None:
        """Update the one authoritative Release body pointer."""
        ...


def _generation_assets(snapshot: SnapshotManifest) -> tuple[PublicationAsset, ...]:
    """Describe DB, checksum, and immutable manifest assets in upload order."""
    manifest_asset = immutable_manifest_asset_name(snapshot.sha256)
    return (
        (snapshot.database, snapshot.database.name),
        (snapshot.checksum, snapshot.checksum.name),
        (snapshot.pointer, manifest_asset),
    )


def _pointer_body(snapshot: SnapshotManifest) -> str:
    """Serialize the strict body pointer for one immutable manifest asset."""
    pointer = ReleasePointer(
        manifest_asset=immutable_manifest_asset_name(snapshot.sha256)
    )
    return pointer.model_dump_json()


def _read_after_pointer_failure(adapter: PublicationAdapter) -> str | None:
    """Resolve an ambiguous edit by observing the Release body once."""
    try:
        return adapter.read_pointer()
    except PublicationError as read_error:
        reason = "pointer_state_unknown"
        raise PublicationError(reason) from read_error


def publish_generation(snapshot: SnapshotManifest, adapter: PublicationAdapter) -> None:
    """Verify all immutable assets before changing one authoritative body pointer.

    GitHub Release assets are not a transaction. A failed upload may leave an
    unreachable partial generation; the pointer is never changed until every
    component verifies. A failed body edit is resolved by one read-back so an
    ambiguous API response cannot create a second authoritative generation.
    """
    prior_pointer = adapter.read_pointer()
    assets = _generation_assets(snapshot)
    for source, asset_name in assets:
        adapter.upload_generation_asset(source, asset_name)
    adapter.verify_generation_assets(assets)

    pointer = _pointer_body(snapshot)
    try:
        adapter.update_pointer(pointer)
    except PublicationError as error:
        observed_pointer = _read_after_pointer_failure(adapter)
        if observed_pointer == pointer:
            return
        if observed_pointer == prior_pointer:
            reason = (
                "initial_pointer_failure" if prior_pointer is None else error.reason
            )
            raise PublicationError(reason) from error
        reason = "pointer_state_unknown"
        raise PublicationError(reason) from error

    try:
        observed_pointer = adapter.read_pointer()
    except PublicationError as error:
        reason = "pointer_readback_failure"
        raise PublicationError(reason) from error
    if observed_pointer != pointer:
        reason = "pointer_readback_mismatch"
        raise PublicationError(reason)
