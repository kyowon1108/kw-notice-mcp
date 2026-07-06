"""GitHub Actions contract tests with no GitHub or KW network access."""

import re
from pathlib import Path
from typing import Protocol

import yaml

ROOT = Path(__file__).parents[2]
_ACTION_NAME = r"[A-Za-z0-9_.-]+"
_ACTION_PREFIX = f"^\\s+uses:\\s+(?P<action>{_ACTION_NAME}/{_ACTION_NAME})@"
_ACTION_SUFFIX = r"(?P<sha>[0-9a-f]{40})\s+#\s+(?P<tag>v\S+)\s*$"
_PINNED_ACTION = re.compile(
    f"{_ACTION_PREFIX}{_ACTION_SUFFIX}",
    re.MULTILINE,
)
_EXPECTED_ACTION_REFERENCES = {
    (
        "actions/checkout",
        "9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0",
        "v7.0.0",
    ),
    (
        "actions/setup-python",
        "ece7cb06caefa5fff74198d8649806c4678c61a1",
        "v6.3.0",
    ),
    (
        "astral-sh/setup-uv",
        "fac544c07dec837d0ccb6301d7b5580bf5edae39",
        "v8.2.0",
    ),
}


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
    assert {
        (match.group("action"), match.group("sha"), match.group("tag"))
        for match in references
    } == _EXPECTED_ACTION_REFERENCES
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
