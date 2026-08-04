"""Focused tests for the repository-local strictness checker."""

from pathlib import Path

from kw_notice_mcp.no_excuse import scan_paths


def test_checker_reports_banned_any_and_type_ignore(tmp_path: Path) -> None:
    """Given banned escape hatches, the checker reports both rule IDs."""
    source = tmp_path / "bad.py"
    source_text = """from typing import Any, cast

def bad(value: Any) -> Any:  # type: ignore
    return cast(Any, value)
"""
    _ = source.write_text(
        source_text,
        encoding="utf-8",
    )

    violations = scan_paths((source,))

    assert {violation.rule for violation in violations} >= {
        "cast-any",
        "type-ignore",
    }


def test_checker_accepts_strict_source(tmp_path: Path) -> None:
    """Given typed source without excuses, the checker reports no violations."""
    source = tmp_path / "good.py"
    source_text = """from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class Value:
    number: int
"""
    _ = source.write_text(
        source_text,
        encoding="utf-8",
    )

    assert scan_paths((source,)) == ()
