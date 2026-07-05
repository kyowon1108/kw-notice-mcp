"""Typer operations for database setup, bounded crawling, status, and STDIO."""

import sqlite3
import uuid
from pathlib import Path
from typing import Annotated

import anyio
import typer
from pydantic import ValidationError

from kw_notice_mcp.cli_logging import configure_logging, log_cli_failure, log_collection
from kw_notice_mcp.collector import Collector
from kw_notice_mcp.collector_models import (
    CollectionResult,
    CollectorConfig,
    CollectStatus,
)
from kw_notice_mcp.server import create_server
from kw_notice_mcp.settings import (
    InvalidConfigurationError,
    Settings,
    SettingsOverrides,
    StatusSnapshot,
    load_settings,
    validate_database_path,
)
from kw_notice_mcp.storage import StorageError, initialize_database, open_storage
from kw_notice_mcp.storage_models import CrawlRun, Freshness
from kw_notice_mcp.storage_support import utc_now
from kw_notice_mcp.wire import HttpxWireTransport, WireTransport

EXIT_SUCCESS = 0
EXIT_BLOCKED = 10
EXIT_BUSY = 11
EXIT_INVALID_CONFIG = 12
EXIT_INFRASTRUCTURE = 13
_EXIT_CODES = {
    CollectStatus.SUCCESS: EXIT_SUCCESS,
    CollectStatus.BLOCKED: EXIT_BLOCKED,
    CollectStatus.BUDGET_EXCEEDED: EXIT_BLOCKED,
    CollectStatus.BUSY: EXIT_BUSY,
}

app = typer.Typer(add_completion=False, no_args_is_help=True)


def _settings(
    overrides: SettingsOverrides,
) -> Settings:
    """Load one command's options through the typed settings boundary."""
    try:
        return load_settings(overrides)
    except (InvalidConfigurationError, ValidationError, TypeError, ValueError) as error:
        reason = "settings"
        raise InvalidConfigurationError(reason) from error


async def _collect(
    store_path: Path,
    config: CollectorConfig,
    run_id: str,
    transport: WireTransport | None,
) -> CollectionResult:
    """Run one collector using either an injected fake or the production adapter."""
    with open_storage(store_path) as store:
        if transport is not None:
            return await Collector(store=store, transport=transport).run(
                run_id=run_id, config=config
            )
        async with HttpxWireTransport() as real_transport:
            return await Collector(store=store, transport=real_transport).run(
                run_id=run_id, config=config
            )


def run_crawl(
    settings: Settings,
    *,
    transport: WireTransport | None = None,
    run_id: str | None = None,
) -> CollectionResult:
    """Execute one bounded crawl; tests may inject a fixture-only wire transport."""
    actual_run_id = run_id or uuid.uuid4().hex
    return anyio.run(
        _collect,
        settings.db_path,
        settings.collector_config(),
        actual_run_id,
        transport,
    )


def _exit_code(result: CollectionResult) -> int:
    """Map the collector's exhaustive result variants to stable CLI codes."""
    return _EXIT_CODES[result.status]


def _write_parent(path: Path) -> None:
    """Create a missing database parent after symlink checks."""
    validate_database_path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        reason = "database parent"
        raise StorageError(reason) from error


def _crawl_state(path: Path, run_id: str) -> CrawlRun | None:
    """Read the completed crawl state for safe structured logging."""
    with open_storage(path) as store:
        return store.get_crawl(run_id)


def _snapshot(settings: Settings) -> StatusSnapshot:
    """Build a useful human-readable status without opening the source site."""
    with open_storage(settings.db_path) as store:
        rows = store.latest(limit=50)
        counts = {freshness.value: 0 for freshness in Freshness}
        for row in rows:
            counts[store.freshness(row.duid, now=utc_now()).value] += 1
        crawl = store.latest_crawl()
        return StatusSnapshot(
            database=str(settings.db_path),
            fts5=True,
            notices=store.latest_count(),
            freshness=counts,
            crawl=crawl.status.value if crawl is not None else "none",
            run_id=crawl.run_id if crawl is not None else None,
            pages=crawl.pages_seen if crawl is not None else 0,
            details=crawl.detail_requests if crawl is not None else 0,
            block_reason=crawl.block_reason if crawl is not None else None,
        )


@app.command("init-db")
def init_db(
    db_path: Path | None = None,
    log_level: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Create the local SQLite schema and require FTS5."""
    try:
        settings = _settings(SettingsOverrides(db_path, log_level, user_agent))
        _write_parent(settings.db_path)
        initialize_database(settings.db_path)
    except (InvalidConfigurationError, StorageError, OSError, sqlite3.Error) as error:
        logger = configure_logging("ERROR")
        invalid = isinstance(error, InvalidConfigurationError)
        log_cli_failure(
            logger,
            "invalid_config" if invalid else "infrastructure",
            "invalid configuration" if invalid else "init_db",
        )
        code = EXIT_INVALID_CONFIG if invalid else EXIT_INFRASTRUCTURE
        raise typer.Exit(code) from error
    typer.echo(f"initialized db={settings.db_path} fts5=available")


@app.command()
def status(
    db_path: Path | None = None,
    log_level: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Show local cache count, freshness sample, and latest crawl state."""
    try:
        settings = _settings(SettingsOverrides(db_path, log_level, user_agent))
        _write_parent(settings.db_path)
        snapshot = _snapshot(settings)
    except (InvalidConfigurationError, StorageError, OSError, sqlite3.Error) as error:
        logger = configure_logging("ERROR")
        invalid = isinstance(error, InvalidConfigurationError)
        log_cli_failure(
            logger,
            "invalid_config" if invalid else "infrastructure",
            "invalid configuration" if invalid else "status",
        )
        code = EXIT_INVALID_CONFIG if invalid else EXIT_INFRASTRUCTURE
        raise typer.Exit(code) from error
    typer.echo(
        " ".join(
            (
                f"db={snapshot.database}",
                f"notices={snapshot.notices}",
                "freshness="
                + ",".join(
                    f"{key}:{value}" for key, value in snapshot.freshness.items()
                ),
                f"fts5={'available' if snapshot.fts5 else 'missing'}",
                f"crawl={snapshot.crawl}",
                f"run_id={snapshot.run_id or '-'}",
                f"pages={snapshot.pages}",
                f"details={snapshot.details}",
                f"block_reason={snapshot.block_reason or '-'}",
            )
        )
    )


@app.command()
def crawl(  # noqa: PLR0913, PLR0917
    db_path: Path | None = None,
    log_level: str | None = None,
    user_agent: str | None = None,
    max_pages: int | None = None,
    max_detail_requests: int | None = None,
    max_duration_seconds: float | None = None,
    metadata_only: Annotated[
        bool,
        typer.Option(
            "--metadata-only",
            help="Require the scheduled metadata-only collection contract.",
        ),
    ] = False,
) -> None:
    """Run one bounded, metadata-only first-page crawl."""
    del metadata_only
    try:
        settings = _settings(
            SettingsOverrides(
                db_path,
                log_level,
                user_agent,
                max_pages,
                max_detail_requests,
                max_duration_seconds,
            )
        )
        result = run_crawl(settings)
        crawl_state = _crawl_state(settings.db_path, result.run_id)
        logger = configure_logging(settings.log_level)
        log_collection(logger, result, crawl_state)
    except (InvalidConfigurationError, StorageError, OSError, sqlite3.Error) as error:
        logger = configure_logging("ERROR")
        invalid = isinstance(error, InvalidConfigurationError)
        log_cli_failure(
            logger,
            "invalid_config" if invalid else "infrastructure",
            "invalid configuration" if invalid else "crawl",
        )
        code = EXIT_INVALID_CONFIG if invalid else EXIT_INFRASTRUCTURE
        raise typer.Exit(code) from error
    raise typer.Exit(_exit_code(result))


@app.command()
def serve(
    db_path: Path | None = None,
    log_level: str | None = None,
) -> None:
    """Serve four read-only MCP tools over local STDIO until stdin closes."""
    try:
        settings = _settings(SettingsOverrides(db_path, log_level))
        _ = configure_logging(settings.log_level)
        create_server(settings.db_path).run(transport="stdio")
    except (InvalidConfigurationError, StorageError, OSError, sqlite3.Error) as error:
        logger = configure_logging("ERROR")
        invalid = isinstance(error, InvalidConfigurationError)
        log_cli_failure(
            logger,
            "invalid_config" if invalid else "infrastructure",
            "invalid configuration" if invalid else "serve",
        )
        code = EXIT_INVALID_CONFIG if invalid else EXIT_INFRASTRUCTURE
        raise typer.Exit(code) from error


def main() -> None:
    """Run the Typer application."""
    app()


if __name__ == "__main__":
    main()
