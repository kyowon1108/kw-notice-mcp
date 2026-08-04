"""Fixture-only integration coverage for the Todo 6 command boundary."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kw_notice_mcp import cli
from kw_notice_mcp.cli import (
    EXIT_INVALID_CONFIG,
    EXIT_SUCCESS,
    app,
    run_crawl,
)
from kw_notice_mcp.collector_models import CollectionResult, CollectStatus
from kw_notice_mcp.settings import Settings
from kw_notice_mcp.storage import StorageError, open_storage
from kw_notice_mcp.wire import WireResponse

runner = CliRunner()
FIXTURES = Path(__file__).parents[1] / "fixtures"


class FakeRobotsWire:
    """Return a deterministic robots response without opening a network socket."""

    response: WireResponse

    def __init__(self, response: WireResponse) -> None:
        self.response = response
        self.calls: list[str] = []

    async def request(self, url: str) -> WireResponse:
        self.calls.append(url)
        return self.response


class QueueWire:
    """Return one deterministic fixture response per request."""

    responses: list[WireResponse]

    def __init__(self, responses: list[WireResponse]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    async def request(self, url: str) -> WireResponse:
        self.calls.append(url)
        return self.responses.pop(0)


def test_root_and_subcommand_help_are_available() -> None:
    """Given the installed CLI, root and every Todo 6 command print help."""
    root = runner.invoke(app, ["--help"])

    assert root.exit_code == EXIT_SUCCESS
    for command in ("init-db", "crawl", "serve", "status"):
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == EXIT_SUCCESS
        assert command in result.stdout
    assert "metadata-only" in runner.invoke(app, ["crawl", "--help"]).stdout


def test_init_db_and_status_report_fts_and_crawl_state(tmp_path: Path) -> None:
    """Given a new path, init-db creates FTS5 and status reports an empty cache."""
    database = tmp_path / "notices.sqlite3"

    initialized = runner.invoke(app, ["init-db", "--db-path", str(database)])
    status = runner.invoke(app, ["status", "--db-path", str(database)])

    assert initialized.exit_code == EXIT_SUCCESS
    assert database.exists()
    assert status.exit_code == EXIT_SUCCESS
    assert "fts5=available" in status.stdout
    assert "notices=0" in status.stdout
    assert "crawl=none" in status.stdout


def test_init_db_infrastructure_failure_returns_clean_exit_13(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Given a forced storage failure, init-db reports only the typed CLI error."""
    database = tmp_path / "notices.sqlite3"

    def fail_initialize(_path: Path | str) -> None:
        raise StorageError

    monkeypatch.setattr(cli, "initialize_database", fail_initialize)

    result = runner.invoke(app, ["init-db", "--db-path", str(database)])

    assert result.exit_code == 13
    assert '"status": "infrastructure"' in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


def test_invalid_config_returns_typed_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Given invalid settings, the CLI returns the documented config exit code."""
    monkeypatch.setenv("KW_NOTICE_MAX_PAGES", "51")
    result = runner.invoke(app, ["status", "--db-path", str(tmp_path / "db.sqlite3")])

    assert result.exit_code == EXIT_INVALID_CONFIG
    assert "invalid configuration" in result.stderr.lower()


def test_invalid_user_agent_and_db_directory_are_rejected(tmp_path: Path) -> None:
    """Given unsafe boundary values, no database operation is attempted."""
    bad_agent = runner.invoke(
        app,
        ["status", "--db-path", str(tmp_path / "db.sqlite3"), "--user-agent", "\n"],
    )
    bad_path = runner.invoke(app, ["init-db", "--db-path", str(tmp_path)])

    assert bad_agent.exit_code == EXIT_INVALID_CONFIG
    assert bad_path.exit_code == EXIT_INVALID_CONFIG


def test_database_symlink_target_is_rejected_when_supported(tmp_path: Path) -> None:
    """Given a database symlink, setup refuses to follow it."""
    target = tmp_path / "target.sqlite3"
    link = tmp_path / "link.sqlite3"
    try:
        link.symlink_to(target)
    except OSError as error:
        error_type = type(error).__name__
        reason = f"symlinks are unavailable: {error_type}"
        pytest.skip(reason)

    result = runner.invoke(app, ["init-db", "--db-path", str(link)])

    assert result.exit_code == EXIT_INVALID_CONFIG


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--max-pages", "51"),
        ("--max-detail-requests", "101"),
        ("--max-duration-seconds", "601"),
    ],
)
def test_invalid_crawl_limits_return_typed_exit_code(
    tmp_path: Path, option: str, value: str
) -> None:
    """Given each over-bound crawl option, crawl exits before any wire call."""
    result = runner.invoke(
        app,
        ["crawl", "--db-path", str(tmp_path / "db.sqlite3"), option, value],
    )

    assert result.exit_code == EXIT_INVALID_CONFIG


def test_blocked_and_busy_results_have_distinct_cli_exit_codes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Given typed collector outcomes, the command maps blocked and busy exactly."""
    database = tmp_path / "db.sqlite3"
    with open_storage(database):
        pass

    blocked = CollectionResult(
        status=CollectStatus.BLOCKED,
        run_id="blocked-run",
        reason="robots_invalid_content_type",
        wire_requests=1,
        retry_count=0,
    )
    busy = CollectionResult(
        status=CollectStatus.BUSY,
        run_id="busy-run",
        reason="busy",
        wire_requests=0,
        retry_count=0,
    )

    def fake_blocked(_settings: Settings) -> CollectionResult:
        return blocked

    def fake_busy(_settings: Settings) -> CollectionResult:
        return busy

    monkeypatch.setattr(cli, "run_crawl", fake_blocked)
    assert runner.invoke(app, ["crawl", "--db-path", str(database)]).exit_code == 10
    monkeypatch.setattr(cli, "run_crawl", fake_busy)
    assert runner.invoke(app, ["crawl", "--db-path", str(database)]).exit_code == 11


def test_crawl_log_is_structured_stderr_without_response_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Given a safe typed result, stderr contains counters but no body text."""
    database = tmp_path / "db.sqlite3"
    with open_storage(database):
        pass
    result = CollectionResult(
        status=CollectStatus.BLOCKED,
        run_id="log-run",
        reason="robots_invalid_content_type",
        wire_requests=1,
        retry_count=0,
    )

    def fake_result(_settings: Settings) -> CollectionResult:
        return result

    monkeypatch.setattr(cli, "run_crawl", fake_result)
    completed = runner.invoke(app, ["crawl", "--db-path", str(database)])

    assert completed.exit_code == 10
    assert '"run_id": "log-run"' in completed.stderr
    assert "page_count" in completed.stderr
    assert "detail_count" in completed.stderr
    assert "robots_invalid_content_type" in completed.stderr
    assert "<html>" not in completed.stderr


def test_html_404_robots_is_blocked_and_notice_state_is_unchanged(
    tmp_path: Path,
) -> None:
    """Given same-title spoof HTML, block before notice state or page 1."""
    database = tmp_path / "notices.sqlite3"
    with open_storage(database) as store:
        before = store.latest_count()

    wire = FakeRobotsWire(
        WireResponse(
            200,
            {"content-type": "text/html"},
            (
                "<html><head><title>HTTP 404 요청하신 페이지가 존재하지 "
                "않습니다.</title></head><body>spoof</body></html>"
            ).encode(),
        )
    )
    result = run_crawl(Settings(db_path=database), transport=wire, run_id="blocked")

    assert result.status is CollectStatus.BLOCKED
    assert wire.calls == ["https://www.kw.ac.kr/robots.txt"]
    with open_storage(database) as store:
        assert store.latest_count() == before
        assert store.get_crawl("blocked") is not None


def test_exact_custom_404_runs_metadata_only_and_returns_success(
    tmp_path: Path,
) -> None:
    """Given the exact custom-404, CLI collects only first-page metadata."""
    database = tmp_path / "notices.sqlite3"
    custom_404 = (FIXTURES / "robots_custom_404_observed.html").read_bytes()
    list_html = (
        Path(__file__).parents[1] / "fixtures" / "board_list_minimal.html"
    ).read_bytes()
    wire = QueueWire(
        [
            WireResponse(200, {"content-type": "text/html"}, custom_404),
            WireResponse(200, {"content-type": "text/html"}, list_html),
        ]
    )

    result = run_crawl(Settings(db_path=database), transport=wire, run_id="metadata")

    assert result.status is CollectStatus.SUCCESS
    assert wire.calls == [
        "https://www.kw.ac.kr/robots.txt",
        "https://www.kw.ac.kr/ko/life/notice.jsp?srCategoryId=&mode=list&searchKey=1&searchVal=&tpage=1",
    ]
    with open_storage(database) as store:
        assert store.latest_count() == 2
        assert all(item.body is None for item in store.latest(limit=50))


def test_busy_crawl_returns_busy_without_a_wire_call(tmp_path: Path) -> None:
    """Given an active lease, the CLI adapter returns busy before collection."""
    database = tmp_path / "notices.sqlite3"
    with open_storage(database) as store:
        _ = store.start_crawl("already-running")

    wire = FakeRobotsWire(WireResponse(200, {"content-type": "text/plain"}, b""))
    result = run_crawl(Settings(db_path=database), transport=wire, run_id="second")

    assert result.status is CollectStatus.BUSY
    assert wire.calls == []


def test_serve_exits_cleanly_on_repeated_stdin_close(tmp_path: Path) -> None:
    """Given EOF twice, each stdio server process exits without stdout pollution."""
    database = tmp_path / "notices.sqlite3"
    init = runner.invoke(app, ["init-db", "--db-path", str(database)])
    assert init.exit_code == EXIT_SUCCESS
    environment = os.environ.copy()

    for _ in range(2):
        completed = subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-m",
                "kw_notice_mcp.cli",
                "serve",
                "--db-path",
                str(database),
            ],
            input=b"",
            capture_output=True,
            env=environment,
            timeout=10,
            check=False,
        )
        assert completed.returncode == EXIT_SUCCESS
        assert completed.stdout == b""
