"""Tests for the bootstrap preflight decision boundary."""

from pathlib import Path

from kw_notice_mcp.bootstrap import (
    PreflightCode,
    PreflightInput,
    RemoteProbe,
    bootstrap_project,
)


class RecordingProjectWriter:
    """Write a marker so tests can observe bootstrap project writes."""

    target: Path
    write_count: int

    def __init__(self, target: Path) -> None:
        self.target = target
        self.write_count = 0

    def write_project_files(self) -> None:
        """Record and perform one representative project-file write."""
        self.write_count += 1
        _ = (self.target / "project-write.marker").write_text(
            "project files written",
            encoding="utf-8",
        )


def _assert_blocked_without_project_write(
    inputs: PreflightInput,
    expected_code: PreflightCode,
    target: Path,
) -> None:
    sentinel = target / "preserve.txt"
    _ = sentinel.write_text("preserve", encoding="utf-8")
    writer = RecordingProjectWriter(target)
    before = tuple(target.iterdir())

    result = bootstrap_project(inputs, writer)

    assert result.code is expected_code
    assert result.project_file_write_allowed is False
    assert writer.write_count == 0
    assert not (target / "project-write.marker").exists()
    assert tuple(target.iterdir()) == before
    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_existing_git_blocks_without_project_file_write(tmp_path: Path) -> None:
    """Given a local Git directory, preflight blocks before writes."""
    _assert_blocked_without_project_write(
        PreflightInput(
            workspace_has_git=True,
            configured_origin=None,
            remote=RemoteProbe(exit_code=0, stdout="", stderr=""),
        ),
        PreflightCode.BLOCKED_EXISTING_GIT,
        tmp_path,
    )


def test_wrong_origin_blocks_without_project_file_write(tmp_path: Path) -> None:
    """Given a wrong configured origin, preflight blocks before writes."""
    _assert_blocked_without_project_write(
        PreflightInput(
            workspace_has_git=False,
            configured_origin="https://example.com/wrong.git",
            remote=RemoteProbe(exit_code=0, stdout="", stderr=""),
        ),
        PreflightCode.BLOCKED_WRONG_ORIGIN,
        tmp_path,
    )


def test_nonempty_remote_blocks_without_project_file_write(tmp_path: Path) -> None:
    """Given remote refs, preflight blocks before writes."""
    _assert_blocked_without_project_write(
        PreflightInput(
            workspace_has_git=False,
            configured_origin=None,
            remote=RemoteProbe(
                exit_code=0, stdout="abc123\trefs/heads/main\n", stderr=""
            ),
        ),
        PreflightCode.BLOCKED_NONEMPTY_REMOTE,
        tmp_path,
    )


def test_remote_lookup_failure_blocks_without_project_file_write(
    tmp_path: Path,
) -> None:
    """Given a failed remote lookup, preflight blocks before writes."""
    _assert_blocked_without_project_write(
        PreflightInput(
            workspace_has_git=False,
            configured_origin=None,
            remote=RemoteProbe(exit_code=128, stdout="", stderr="lookup failed"),
        ),
        PreflightCode.BLOCKED_REMOTE_LOOKUP_FAILURE,
        tmp_path,
    )


def test_remote_output_blocks_without_project_file_write(tmp_path: Path) -> None:
    """Given any stdout from the remote probe, preflight blocks before writes."""
    _assert_blocked_without_project_write(
        PreflightInput(
            workspace_has_git=False,
            configured_origin=None,
            remote=RemoteProbe(exit_code=0, stdout="unexpected", stderr=""),
        ),
        PreflightCode.BLOCKED_REMOTE_OUTPUT,
        tmp_path,
    )


def test_clean_preflight_writes_project_once(tmp_path: Path) -> None:
    """Given a clean preflight, bootstrap invokes the project writer once."""
    writer = RecordingProjectWriter(tmp_path)

    result = bootstrap_project(
        PreflightInput(
            workspace_has_git=False,
            configured_origin=None,
            remote=RemoteProbe(exit_code=0, stdout="", stderr=""),
        ),
        writer,
    )

    assert result.code is PreflightCode.ALLOWED
    assert result.project_file_write_allowed is True
    assert writer.write_count == 1
    assert (tmp_path / "project-write.marker").read_text(encoding="utf-8") == (
        "project files written"
    )
