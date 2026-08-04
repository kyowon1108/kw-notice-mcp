"""Versioned deterministic redaction for human-authored notice fields."""

import re
from dataclasses import dataclass
from typing import Final

from kw_notice_mcp.normalization import normalize_text

REDACTION_VERSION: Final[str] = "v1"
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.-])")
_RESIDENT = re.compile(r"(?<!\d)\d{6}[- ]\d{7}(?!\d)")
_KOREAN_MOBILE = re.compile(r"(?<!\d)01[016789][- ]?\d{3,4}[- ]?\d{4}(?!\d)")
_KOREAN_AREA_CODE = (
    r"(?:02|031|032|033|041|042|043|044|051|052|053|054|055|061|062|063|064)"
)
_KOREAN_LANDLINE = re.compile(
    rf"(?<!\d)\(?{_KOREAN_AREA_CODE}\)?[- ]?\d{{3,4}}[- ]?\d{{4}}(?!\d)"
)
_KOREAN_SERVICE = re.compile(
    r"(?<!\d)(?:1[568]\d{2}[- ]\d{4}|0(?:50|60|70|80)[- ]?\d{3,4}[- ]\d{4})(?!\d)"
)
_INTERNATIONAL_PREFIX = r"(?<!\d)\+\d{1,3}[ -]?\d{1,4}"
_INTERNATIONAL_SUFFIX = r"(?:[ -]?\d{3,4}){2}(?!\d)"
_INTERNATIONAL_PHONE = re.compile(f"{_INTERNATIONAL_PREFIX}{_INTERNATIONAL_SUFFIX}")
_LABELLED_PREFIX = r"(?P<label>학번|사번|계좌|주민등록번호)"
_LABELLED_SEPARATOR = r"(?P<separator>\s*(?:번호\s*)?[:\uFF1A=#-]?\s*)"
_LABELLED_VALUE = r"(?P<value>[A-Za-z0-9][A-Za-z0-9 -]{2,19})"
_LABELLED = re.compile(f"{_LABELLED_PREFIX}{_LABELLED_SEPARATOR}{_LABELLED_VALUE}")
_ACCOUNT_RUN = re.compile(r"(?<![\d-])\d{10,14}(?![\d-])")


@dataclass(frozen=True, slots=True)
class RedactedHumanFields:
    """All bounded human-authored fields after the v1 privacy pass."""

    title: str
    category_name: str
    department: str
    body: str


def _replace_account_run(match: re.Match[str]) -> str:
    """Mask an account-like run unless it is explicitly a notice number."""
    start = max(0, match.start() - 8)
    context = match.string[start : match.start()]
    if "공지" in context or "notice" in context.lower():
        return match.group(0)
    return "[REDACTED_ACCOUNT]"


def redact_text(value: str) -> str:
    """Redact v1 email, phone, resident-ID, and identifier patterns."""
    redacted = _LABELLED.sub(r"\g<label>\g<separator>[REDACTED_IDENTIFIER]", value)
    redacted = _RESIDENT.sub("[REDACTED_RESIDENT_ID]", redacted)
    redacted = _EMAIL.sub("[REDACTED_EMAIL]", redacted)
    redacted = _KOREAN_MOBILE.sub("[REDACTED_PHONE]", redacted)
    redacted = _KOREAN_LANDLINE.sub("[REDACTED_PHONE]", redacted)
    redacted = _KOREAN_SERVICE.sub("[REDACTED_PHONE]", redacted)
    redacted = _INTERNATIONAL_PHONE.sub("[REDACTED_PHONE]", redacted)
    return _ACCOUNT_RUN.sub(_replace_account_run, redacted)


def _redact_and_cap(value: str, limit: int) -> str:
    return normalize_text(redact_text(value), limit)


def redact_human_fields(
    *, title: str, category_name: str, department: str, body: str
) -> RedactedHumanFields:
    """Redact and cap every persisted human-authored field."""
    return RedactedHumanFields(
        title=_redact_and_cap(title, 500),
        category_name=_redact_and_cap(category_name, 64),
        department=_redact_and_cap(department, 200),
        body=_redact_and_cap(body, 4000),
    )
