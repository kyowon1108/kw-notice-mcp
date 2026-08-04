"""Atomic local generations and checksum-verifying consumer installation."""

import hashlib
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final, NoReturn

from pydantic import ValidationError

from kw_notice_mcp.release_models import (
    ReleaseManifest,
    ReleasePointer,
    SnapshotError,
    SnapshotManifest,
)
from kw_notice_mcp.storage_schema import schema_is_valid
from kw_notice_mcp.storage_support import fetch_one

POINTER_ASSET: Final = "notices-manifest.json"


def immutable_manifest_asset_name(sha256: str) -> str:
    """Return the content-addressed asset name for one validated digest."""
    return f"notices-manifest-{sha256}.json"


def _invalid(reason: str) -> NoReturn:
    """Raise one typed snapshot failure."""
    raise SnapshotError(reason)


def _sha256(path: Path) -> str:
    """Hash one local file without loading it all into memory."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        reason = "database_read"
        raise SnapshotError(reason) from error
    return digest.hexdigest()


def _write_checksum(path: Path, content: str) -> None:
    """Write the checksum component through one injectable filesystem seam."""
    _ = path.write_text(content, encoding="ascii")


def _validate_database(database: Path) -> None:
    """Validate SQLite integrity/schema and reject retained body content."""
    if not database.is_file() or database.is_symlink() or database.stat().st_size == 0:
        _invalid("database_file")
    uri = f"file:{database.absolute()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            if fetch_one(connection, "PRAGMA integrity_check") != ("ok",):
                _invalid("integrity")
            if not schema_is_valid(connection):
                _invalid("schema")
            body_count = fetch_one(
                connection, "SELECT COUNT(*) FROM notices WHERE body IS NOT NULL"
            )
            if body_count != (0,):
                _invalid("body_present")
            fts_body_count = fetch_one(
                connection,
                "SELECT COUNT(*) FROM notices_fts WHERE body IS NOT NULL AND body != ?",
                ("",),
            )
            if fts_body_count != (0,):
                _invalid("fts_body_present")
    except sqlite3.Error as error:
        reason = "sqlite"
        raise SnapshotError(reason) from error


def load_manifest(path: Path) -> ReleaseManifest:
    """Parse the untrusted pointer through the strict Pydantic boundary."""
    try:
        return ReleaseManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError) as error:
        reason = "manifest"
        raise SnapshotError(reason) from error


def load_pointer(path: Path) -> ReleasePointer:
    """Parse a GitHub Release body pointer through the strict boundary."""
    try:
        return ReleasePointer.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError) as error:
        reason = "pointer"
        raise SnapshotError(reason) from error


def verify_pair(
    database: Path,
    checksum: Path,
    *,
    expected_sha256: str,
) -> None:
    """Require a complete pair whose checksum and content match the pointer."""
    if not database.is_file() or not checksum.is_file():
        _invalid("incomplete_pair")
    if database.is_symlink() or checksum.is_symlink():
        _invalid("asset_symlink")
    expected_line = f"{expected_sha256}  {database.name}\n"
    try:
        checksum_text = checksum.read_text(encoding="ascii")
    except (OSError, UnicodeError) as error:
        reason = "checksum_read"
        raise SnapshotError(reason) from error
    if checksum_text != expected_line or _sha256(database) != expected_sha256:
        _invalid("checksum_mismatch")


def _generation_manifest(generation_dir: Path) -> SnapshotManifest:
    """Load and verify one already committed generation directory."""
    pointer = generation_dir / POINTER_ASSET
    manifest = load_manifest(pointer)
    database = generation_dir / manifest.database_asset
    checksum = generation_dir / manifest.checksum_asset
    verify_pair(database, checksum, expected_sha256=manifest.sha256)
    return SnapshotManifest(
        database=database,
        checksum=checksum,
        pointer=pointer,
        generation_dir=generation_dir,
        sha256=manifest.sha256,
    )


def prepare_snapshot(database: Path, output_dir: Path) -> SnapshotManifest:
    """Commit a complete generation with one same-filesystem directory rename.

    The atomicity boundary is the final rename of the fully populated staging
    directory into ``generations/<sha256>``. Before that rename no generation
    path is visible; after it, DB, checksum, and pointer are visible together.
    """
    _validate_database(database)
    generations = output_dir / "generations"
    _ = generations.mkdir(parents=True, exist_ok=True)
    try:
        with TemporaryDirectory(prefix=".staging-", dir=generations) as temporary:
            staging = Path(temporary)
            staged_database = staging / "database.tmp"
            _ = shutil.copyfile(database, staged_database)
            digest = _sha256(staged_database)
            database_asset = f"notices-{digest}.sqlite3"
            checksum_asset = f"{database_asset}.sha256"
            committed_database = staging / database_asset
            _ = staged_database.replace(committed_database)
            committed_checksum = staging / checksum_asset
            _write_checksum(committed_checksum, f"{digest}  {database_asset}\n")
            pointer = staging / POINTER_ASSET
            manifest = ReleaseManifest(
                sha256=digest,
                database_asset=database_asset,
                checksum_asset=checksum_asset,
            )
            _ = pointer.write_text(
                f"{manifest.model_dump_json(indent=2)}\n", encoding="utf-8"
            )
            verify_pair(
                committed_database,
                committed_checksum,
                expected_sha256=digest,
            )
            target = generations / digest
            if target.exists():
                return _generation_manifest(target)
            _ = staging.replace(target)
        return _generation_manifest(target)
    except SnapshotError:
        raise
    except OSError as error:
        reason = "generation_write"
        raise SnapshotError(reason) from error


def install_release(
    manifest_path: Path,
    assets_dir: Path,
    destination: Path,
) -> str:
    """Verify a complete generation before atomically replacing the local DB."""
    manifest = load_manifest(manifest_path)
    database = assets_dir / manifest.database_asset
    checksum = assets_dir / manifest.checksum_asset
    verify_pair(database, checksum, expected_sha256=manifest.sha256)
    if destination.is_symlink():
        _invalid("destination_symlink")
    _ = destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with TemporaryDirectory(
            prefix=".kw-notice-install-", dir=destination.parent
        ) as temporary:
            candidate = Path(temporary) / destination.name
            _ = shutil.copyfile(database, candidate)
            if _sha256(candidate) != manifest.sha256:
                _invalid("copy_mismatch")
            _validate_database(candidate)
            _ = candidate.replace(destination)
    except OSError as error:
        reason = "install_write"
        raise SnapshotError(reason) from error
    return manifest.sha256
