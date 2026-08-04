# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
# ─── How to run ───
# uv run python scripts/check_no_excuse_rules.py src tests
"""Run the repository-local no-excuse checker."""

import sys
from pathlib import Path

from kw_notice_mcp.no_excuse import RuleViolation, scan_paths


def _format_violation(violation: RuleViolation) -> str:
    return f"{violation.path}:{violation.line}: {violation.rule}: {violation.message}"


def main(arguments: tuple[str, ...]) -> int:
    """Scan the supplied source roots and return a process status."""
    paths = tuple(Path(argument) for argument in arguments) or (
        Path("src"),
        Path("tests"),
    )
    violations = scan_paths(paths)
    for violation in violations:
        print(_format_violation(violation))
    return int(bool(violations))


if __name__ == "__main__":
    raise SystemExit(main(tuple(sys.argv[1:])))
