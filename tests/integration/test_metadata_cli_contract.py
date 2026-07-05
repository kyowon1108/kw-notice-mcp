"""Explicit scheduled metadata-only CLI contract tests."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from kw_notice_mcp import cli
from kw_notice_mcp.cli import EXIT_SUCCESS, app
from kw_notice_mcp.collector_models import CollectionResult, CollectStatus
from kw_notice_mcp.settings import Settings
from kw_notice_mcp.storage import open_storage

runner = CliRunner()


def test_explicit_metadata_only_flag_is_accepted_without_live_collection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Given the refresh flag, the CLI accepts the explicit metadata contract."""
    database = tmp_path / "notices.sqlite3"
    with open_storage(database):
        pass

    def fake_crawl(_settings: Settings) -> CollectionResult:
        return CollectionResult(
            status=CollectStatus.SUCCESS,
            run_id="metadata-flag",
            reason=None,
            wire_requests=0,
            retry_count=0,
        )

    monkeypatch.setattr(cli, "run_crawl", fake_crawl)

    result = runner.invoke(
        app,
        ["crawl", "--metadata-only", "--db-path", str(database)],
    )

    assert result.exit_code == EXIT_SUCCESS
