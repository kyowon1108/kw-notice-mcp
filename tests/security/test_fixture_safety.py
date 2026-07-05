"""Disk safety checks for synthetic fixtures."""

import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]
FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures"
SOURCE_TEXT_ROOTS = (REPOSITORY_ROOT / "src", REPOSITORY_ROOT / "tests")
SOURCE_TEXT_SUFFIXES = frozenset(
    {".html", ".json", ".md", ".py", ".pyi", ".toml", ".txt", ".yaml", ".yml"}
)
_PHONE_PREFIX = r"(?<!\d)(?:01[016789][ -]?\d{3,4}[ -]?\d{4}|"
_PHONE_SUFFIX = r"\+\d{1,3}[ -]?\d{1,4}(?:[ -]?\d{3,4}){2})(?!\d)"
_LABELLED_PREFIX = r"(?:학번|사번|계좌|주민등록번호)"
_LABELLED_SUFFIX = r"\s*(?:번호\s*)?[:\uFF1A=#-]?\s*[A-Za-z0-9][A-Za-z0-9 -]{2,19}"
FORBIDDEN_PATTERNS = (
    ("email", re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")),
    ("phone", re.compile(f"{_PHONE_PREFIX}{_PHONE_SUFFIX}")),
    ("resident", re.compile(r"(?<!\d)\d{6}[- ]\d{7}(?!\d)")),
    (
        "labelled_identifier",
        re.compile(f"{_LABELLED_PREFIX}{_LABELLED_SUFFIX}"),
    ),
)
ACCOUNT_LIKE = re.compile(r"(?<![\d-])\d{10,14}(?![\d-])")


def _source_text_files() -> tuple[Path, ...]:
    """Return repository source/test text while excluding generated caches."""
    return tuple(
        sorted(
            path
            for root in SOURCE_TEXT_ROOTS
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix in SOURCE_TEXT_SUFFIXES
            and "__pycache__" not in path.parts
        )
    )


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _is_explicit_notice_number(text: str, offset: int) -> bool:
    context = text[max(0, offset - 16) : offset]
    return "공지번호" in context


def _repository_safety_violations() -> tuple[str, ...]:
    violations: list[str] = []
    for path in _source_text_files():
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(REPOSITORY_ROOT)
        violations.extend(
            f"{relative}:{_line_number(text, match.start())}:{name}"
            for name, pattern in FORBIDDEN_PATTERNS
            for match in pattern.finditer(text)
        )
        violations.extend(
            f"{relative}:{_line_number(text, match.start())}:account_like"
            for match in ACCOUNT_LIKE.finditer(text)
            if not _is_explicit_notice_number(text, match.start())
        )
    return tuple(violations)


def test_synthetic_fixtures_contain_no_sensitive_or_attachment_content() -> None:
    """Given committed fixture files, the safety scan finds no sensitive markers."""
    fixture_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in FIXTURE_ROOT.rglob("*")
        if path.is_file()
    )

    assert all(
        pattern.search(fixture_text) is None for _, pattern in FORBIDDEN_PATTERNS
    )
    assert all(
        _is_explicit_notice_number(fixture_text, match.start())
        for match in ACCOUNT_LIKE.finditer(fixture_text)
    )
    assert "attachment content" not in fixture_text.lower()
    assert "raw page" not in fixture_text.lower()


def test_repository_source_text_contains_no_complete_sensitive_sentinel() -> None:
    """Given source and tests, no complete positive sentinel is stored on disk."""
    assert _repository_safety_violations() == ()
