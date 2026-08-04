"""GitHub Actions contract tests with no GitHub or KW network access."""

import re
from pathlib import Path
from typing import Protocol

import yaml

from kw_notice_mcp.release import publication_decision

ROOT = Path(__file__).parents[2]
_ACTION_NAME = r"[A-Za-z0-9_.-]+"
_ACTION_PREFIX = f"^\\s+uses:\\s+(?P<action>{_ACTION_NAME}/{_ACTION_NAME})@"
_ACTION_SUFFIX = r"(?P<sha>[0-9a-f]{40})\s+#\s+v\S+\s*$"
_PINNED_ACTION = re.compile(
    f"{_ACTION_PREFIX}{_ACTION_SUFFIX}",
    re.MULTILINE,
)


class _YamlNode(Protocol):
    """Opaque parsed YAML node used only to prove syntax validity."""


class _YamlApi(Protocol):
    """Typed subset of PyYAML needed by the syntax assertion."""

    def compose(self, stream: str) -> _YamlNode | None:
        """Parse one YAML document into an opaque node."""
        ...


_YAML: _YamlApi = yaml


def _parse_workflow(name: str) -> str:
    """Parse one workflow as YAML and return its source for contract assertions."""
    path = ROOT / ".github" / "workflows" / name
    source = path.read_text(encoding="utf-8")
    assert _YAML.compose(source) is not None
    return source


def test_ci_is_locked_read_only_and_runs_all_local_checks() -> None:
    """Given CI YAML, it is read-only and contains every exact local check."""
    source = _parse_workflow("ci.yml")

    assert "pull_request:" in source
    assert "permissions:" in source
    assert "contents: read" in source
    assert "uv sync --locked --dev" in source
    assert "uv.lock" in source
    for command in (
        "uv run python scripts/check_no_excuse_rules.py src tests",
        "uv run ruff check",
        "uv run ruff format --check",
        "uv run basedpyright",
        "uv run pytest -q",
        "tests/integration/test_mcp.py -q -k all_four_tools_over_stdio",
    ):
        assert command in source
    assert "kw-notice-mcp crawl" not in source
    assert "gh release" not in source
    assert (
        'build_dir=$(mktemp -d "${RUNNER_TEMP:?}/kw-notice-mcp-build.XXXXXX")' in source
    )
    assert 'uv build --out-dir "$build_dir"' in source
    assert 'rm -rf -- "$build_dir"' in source
    assert 'test ! -e "$build_dir"' in source
    assert "test ! -e dist" in source
    assert "test ! -e build" in source


def test_every_workflow_action_uses_a_commented_full_commit_sha() -> None:
    """Given repository workflows, every action reference is immutable and reviewed."""
    workflow_sources = tuple(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    )
    references = tuple(
        match
        for source in workflow_sources
        for match in _PINNED_ACTION.finditer(source)
    )

    assert references
    assert all(match.group("sha") == match.group("sha").lower() for match in references)
    assert all(
        source_line.strip().startswith("uses:")
        for source in workflow_sources
        for source_line in source.splitlines()
        if "uses:" in source_line
    )
    assert sum(source.count("uses:") for source in workflow_sources) == len(references)


def test_dependabot_controls_github_action_updates() -> None:
    """Given Dependabot configuration, GitHub Actions updates are weekly and bounded."""
    source = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")

    assert _YAML.compose(source) is not None
    assert "package-ecosystem: github-actions" in source
    assert 'directory: "/"' in source
    assert "interval: weekly" in source
    assert "open-pull-requests-limit: 5" in source


def test_refresh_schedule_and_publication_are_success_gated() -> None:
    """Given refresh YAML, schedule and publication are bounded."""
    source = _parse_workflow("refresh-metadata.yml")

    assert "workflow_dispatch:" in source
    assert "cron: '7,22,37,52 0-8 * * 1-5'" in source
    assert "cancel-in-progress: false" in source
    workflow_header, jobs = source.split("jobs:", maxsplit=1)
    assert "contents: read" in workflow_header
    assert "contents: write" not in workflow_header
    assert "contents: write" in jobs
    assert jobs.count("contents: write") == 1
    assert "  refresh:\n    runs-on:" in jobs
    assert (
        'gh release view data-latest --repo "$GITHUB_REPOSITORY" --json body,assets'
        in source
    )
    assert "verify-pointer" in source
    assert "gh release download data-latest" in source
    assert "kw-notice-mcp init-db" in source
    assert "kw-notice-mcp crawl --metadata-only" in source
    assert "kw-notice-mcp crawl" in source
    assert "kw_notice_mcp.release verify-manifest" in source
    assert "kw_notice_mcp.release restore" in source
    assert "database_asset" in source
    assert "checksum_asset" in source
    assert ".sha256" in source
    assert "notices-manifest-" in source
    assert "gh release upload data-latest" in source
    assert "gh release edit data-latest" in source
    assert "--notes-file" in source
    assert "--clobber" not in source
    assert "if: steps.crawl.outputs.exit_code == '0'" in source
    assert "pull_request:" not in source
    assert "actions/upload-artifact" not in source

    database_upload = source.index(
        'ensure_generation_asset "$database_path" "$database_asset"'
    )
    checksum_upload = source.index(
        'ensure_generation_asset "$checksum_path" "$checksum_asset"'
    )
    manifest_upload = source.index(
        'ensure_generation_asset "$manifest" "$manifest_asset"'
    )
    pointer_edit = source.index("gh release edit data-latest")
    assert database_upload < manifest_upload < pointer_edit
    assert checksum_upload < manifest_upload

    assert publication_decision(0).publish is True
    assert all(
        publication_decision(exit_code).publish is False
        for exit_code in (10, 11, 12, 13)
    )
