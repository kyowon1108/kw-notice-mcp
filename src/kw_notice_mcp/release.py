"""Network-free CLI and façade for the Release generation protocol."""

from pathlib import Path
from typing import Annotated

import typer

from kw_notice_mcp.release_models import (
    PublicationDecision,
    RefreshDecision,
    ReleaseManifest,
    ReleasePointer,
    RestoreAction,
    RestoreObservation,
    SnapshotError,
    SnapshotManifest,
    execute_refresh,
    publication_decision,
    refresh_decision,
    restore_decision,
)
from kw_notice_mcp.release_publish import (
    PublicationAdapter,
    PublicationError,
    publish_generation,
)
from kw_notice_mcp.release_snapshot import (
    POINTER_ASSET,
    immutable_manifest_asset_name,
    install_release,
    load_manifest,
    load_pointer,
    prepare_snapshot,
    verify_pair,
)

__all__ = [
    "POINTER_ASSET",
    "PublicationAdapter",
    "PublicationDecision",
    "PublicationError",
    "RefreshDecision",
    "ReleaseManifest",
    "ReleasePointer",
    "RestoreAction",
    "RestoreObservation",
    "SnapshotError",
    "SnapshotManifest",
    "execute_refresh",
    "immutable_manifest_asset_name",
    "install_release",
    "load_manifest",
    "load_pointer",
    "prepare_snapshot",
    "publication_decision",
    "publish_generation",
    "refresh_decision",
    "restore_decision",
    "verify_pair",
]

app = typer.Typer(add_completion=False)


@app.command()
def prepare(
    database: Annotated[Path, typer.Option("--database")],
    output_dir: Annotated[Path, typer.Option("--output-dir")],
) -> None:
    """Prepare and print one committed generation pointer path."""
    try:
        snapshot = prepare_snapshot(database, output_dir)
    except SnapshotError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(13) from error
    typer.echo(snapshot.pointer)


@app.command("verify-manifest")
def verify_manifest(
    manifest: Annotated[Path, typer.Option("--manifest")],
) -> None:
    """Validate an untrusted pointer and print canonical JSON."""
    try:
        parsed = load_manifest(manifest)
    except SnapshotError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(13) from error
    typer.echo(parsed.model_dump_json())


@app.command("verify-pointer")
def verify_pointer(
    pointer: Annotated[Path, typer.Option("--pointer")],
) -> None:
    """Validate a Release body pointer and print canonical JSON."""
    try:
        parsed = load_pointer(pointer)
    except SnapshotError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(13) from error
    typer.echo(parsed.model_dump_json())


@app.command()
def restore(
    manifest: Annotated[Path, typer.Option("--manifest")],
    assets_dir: Annotated[Path, typer.Option("--assets-dir")],
    database: Annotated[Path, typer.Option("--database")],
) -> None:
    """Verify downloaded generation assets before replacing the working DB."""
    try:
        digest = install_release(manifest, assets_dir, database)
    except SnapshotError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(13) from error
    typer.echo(f"restored sha256={digest}")


if __name__ == "__main__":
    app()
