"""AST patterns for strictness rules that span control-flow variants."""

import ast
from pathlib import Path

from kw_notice_mcp.no_excuse_models import RuleViolation


def _call_name(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def broad_except_violations(path: Path, node: ast.ExceptHandler) -> list[RuleViolation]:
    """Find catches of the two broad built-in exception classes."""
    if _call_name(node.type) in {"BaseException", "Exception"}:
        return [
            RuleViolation(
                path, node.lineno, "broad-except", "catch a specific exception"
            )
        ]
    return []


def match_violations(path: Path, node: ast.Match) -> list[RuleViolation]:
    """Require an assert_never default case for match statements."""
    for case in node.cases:
        if (
            not isinstance(case.pattern, ast.MatchAs)
            or case.pattern.pattern is not None
        ):
            continue
        if any(
            isinstance(item, ast.Expr)
            and isinstance(item.value, ast.Call)
            and _call_name(item.value.func) == "assert_never"
            for statement in case.body
            for item in ast.walk(statement)
        ):
            return []
    return [
        RuleViolation(
            path, node.lineno, "missing-assert-never", "match needs assert_never"
        )
    ]


def variant_if_violations(path: Path, node: ast.If) -> list[RuleViolation]:
    """Find isinstance or enum-comparison if/elif chains."""
    if not _is_variant_test(node.test):
        return []
    current = node.orelse
    while len(current) == 1 and isinstance(current[0], ast.If):
        if _is_variant_test(current[0].test):
            return [
                RuleViolation(path, node.lineno, "if-elif-on-variant", "use match/case")
            ]
        current = current[0].orelse
    return []


def _is_variant_test(node: ast.expr) -> bool:
    if isinstance(node, ast.Call) and _call_name(node.func) == "isinstance":
        return True
    if not isinstance(node, ast.Compare) or len(node.ops) != 1:
        return False
    if not isinstance(node.ops[0], (ast.Eq, ast.Is)):
        return False
    return isinstance(node.left, ast.Attribute) or isinstance(
        node.comparators[0], ast.Attribute
    )
