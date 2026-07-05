"""Repository-local AST checks for strict Python source rules."""

import ast
import io
import tokenize
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Final

from kw_notice_mcp.no_excuse_models import RuleViolation
from kw_notice_mcp.no_excuse_patterns import (
    broad_except_violations,
    match_violations,
    variant_if_violations,
)

_GENERIC_ERRORS: Final = frozenset(
    {"KeyError", "RuntimeError", "TypeError", "ValueError"}
)
_MAX_PURE_LINES: Final = 250


def _violation(path: Path, node: ast.AST, rule: str, message: str) -> RuleViolation:
    return RuleViolation(path, getattr(node, "lineno", 1), rule, message)


def _python_files(paths: Sequence[Path]) -> Iterable[Path]:
    for path in paths:
        if path.is_file() and path.suffix == ".py":
            yield path
        elif path.is_dir():
            yield from sorted(path.glob("**/*.py"))


def _dataclass_violations(path: Path, node: ast.ClassDef) -> list[RuleViolation]:
    dataclass_calls: list[ast.Call | None] = []
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Name) and decorator.id == "dataclass":
            dataclass_calls.append(None)
        elif (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
            and decorator.func.id == "dataclass"
        ):
            dataclass_calls.append(decorator)
    violations: list[RuleViolation] = []
    for call in dataclass_calls:
        if call is None:
            violations.extend(
                (
                    _violation(
                        path, node, "mutable-dataclass", "dataclass must be frozen"
                    ),
                    _violation(path, node, "missing-slots", "dataclass must use slots"),
                )
            )
            continue
        keywords = {
            item.arg: item.value for item in call.keywords if item.arg is not None
        }
        frozen = keywords.get("frozen")
        slots = keywords.get("slots")
        if not (
            isinstance(frozen, ast.Constant)
            and ast.dump(frozen) == "Constant(value=True)"
        ):
            violations.append(
                _violation(path, node, "mutable-dataclass", "dataclass must be frozen")
            )
        if not (
            isinstance(slots, ast.Constant)
            and ast.dump(slots) == "Constant(value=True)"
        ):
            violations.append(
                _violation(path, node, "missing-slots", "dataclass must use slots")
            )
    return violations


def _node_violations(path: Path, node: ast.AST) -> list[RuleViolation]:
    violations = _basic_node_violations(path, node)
    if isinstance(node, ast.Import):
        violations.extend(_import_violations(path, node))
    if isinstance(node, ast.ImportFrom):
        violations.extend(_import_from_violations(path, node))
    if isinstance(node, ast.ExceptHandler):
        violations.extend(_except_violations(path, node))
    if isinstance(node, ast.Raise):
        violations.extend(_raise_violations(path, node))
    if isinstance(node, ast.ExceptHandler):
        violations.extend(broad_except_violations(path, node))
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        violations.extend(_function_violations(path, node))
    if isinstance(node, ast.ClassDef):
        violations.extend(_dataclass_violations(path, node))
    if isinstance(node, ast.Match):
        violations.extend(match_violations(path, node))
    if isinstance(node, ast.If):
        violations.extend(variant_if_violations(path, node))
    return violations


def _basic_node_violations(path: Path, node: ast.AST) -> list[RuleViolation]:
    violations: list[RuleViolation] = []
    if isinstance(node, ast.Name) and node.id == "Any":
        violations.append(_violation(path, node, "cast-any", "Any is not permitted"))
    if isinstance(node, ast.Name) and node.id == "object":
        violations.append(
            _violation(path, node, "no-object", "object annotations are not permitted")
        )
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "cast"
        and node.args
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "Any"
    ):
        violations.append(
            _violation(path, node, "cast-any", "cast(Any, ...) is not permitted")
        )
    return violations


def _import_violations(path: Path, node: ast.Import) -> list[RuleViolation]:
    violations: list[RuleViolation] = []
    for alias in node.names:
        if alias.name in {"asyncio", "pandas"}:
            rule = "no-asyncio" if alias.name == "asyncio" else "no-pandas"
            violations.append(
                _violation(path, node, rule, f"{alias.name} is not permitted")
            )
    return violations


def _import_from_violations(path: Path, node: ast.ImportFrom) -> list[RuleViolation]:
    if node.module not in {"asyncio", "pandas"}:
        return []
    rule = "no-asyncio" if node.module == "asyncio" else "no-pandas"
    return [_violation(path, node, rule, f"{node.module} is not permitted")]


def _except_violations(path: Path, node: ast.ExceptHandler) -> list[RuleViolation]:
    violations: list[RuleViolation] = []
    if node.type is None:
        violations.append(
            _violation(path, node, "bare-except", "exception type is required")
        )
    if node.body and all(isinstance(item, ast.Pass | ast.Expr) for item in node.body):
        violations.append(
            _violation(path, node, "silent-except", "exception must be handled")
        )
    return violations


def _raise_violations(path: Path, node: ast.Raise) -> list[RuleViolation]:
    if not isinstance(node.exc, ast.Call):
        return []
    exception_name = _call_name(node.exc.func)
    if exception_name not in _GENERIC_ERRORS or not node.exc.args:
        return []
    if all(_is_string_expression(argument) for argument in node.exc.args):
        return [_violation(path, node, "generic-exception", "use a typed error")]
    return []


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_string_expression(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Constant) and isinstance(node.value, str)
    ) or isinstance(node, ast.JoinedStr)


def _function_violations(
    path: Path, node: ast.FunctionDef | ast.AsyncFunctionDef
) -> list[RuleViolation]:
    if isinstance(node.returns, ast.Name) and node.returns.id == "dict":
        return [_violation(path, node, "raw-dict-return", "return a typed model")]
    return []


def _comment_violations(path: Path, source: str) -> list[RuleViolation]:
    violations: list[RuleViolation] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        if "type: ignore" in token.string:
            violations.append(
                RuleViolation(
                    path,
                    token.start[0],
                    "type-ignore",
                    "type ignores are not permitted",
                )
            )
        if "pyright: ignore" in token.string:
            violations.append(
                RuleViolation(
                    path,
                    token.start[0],
                    "pyright-ignore",
                    "pyright ignores are not permitted",
                )
            )
    return violations


def _tree_violations(path: Path, tree: ast.AST, source: str) -> list[RuleViolation]:
    violations = [
        violation
        for node in ast.walk(tree)
        for violation in _node_violations(path, node)
    ]
    return [*violations, *_comment_violations(path, source)]


def _file_violations(path: Path) -> list[RuleViolation]:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as error:
        return [
            RuleViolation(
                path, error.lineno or 1, "syntax-error", "source is not valid Python"
            )
        ]
    violations = _tree_violations(path, tree, source)
    pure_lines = sum(
        1
        for line in source.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    if pure_lines > _MAX_PURE_LINES:
        violations.append(
            RuleViolation(path, 1, "oversized-module", "module exceeds 250 pure LOC")
        )
    return violations


def scan_paths(paths: Sequence[Path]) -> tuple[RuleViolation, ...]:
    """Scan Python files under paths and return stable violations."""
    violations = [
        violation
        for path in _python_files(paths)
        for violation in _file_violations(path)
    ]
    return tuple(
        sorted(violations, key=lambda item: (str(item.path), item.line, item.rule))
    )
