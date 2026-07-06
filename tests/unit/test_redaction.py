"""Behavior tests for versioned v1 privacy redaction and bounds."""

from datetime import date
from pathlib import Path

import pytest

from kw_notice_mcp.domain import CategoryId, CategoryName, NoticeDetail, NoticeSummary
from kw_notice_mcp.redaction import REDACTION_VERSION, redact_human_fields, redact_text
from kw_notice_mcp.storage import open_storage
from kw_notice_mcp.values import DUID, SourceURL


def _runtime_value(*fragments: str) -> str:
    return "".join(fragments)


def test_email_is_redacted_in_memory() -> None:
    """Given an email sentinel held only in memory, redaction replaces it."""
    email = _runtime_value("private.person", "@", "example.invalid")
    value = f"Contact {email} for details."

    redacted = redact_text(value)

    assert email not in redacted
    assert "[REDACTED_EMAIL]" in redacted


def test_phone_is_redacted_in_memory() -> None:
    """Given Korean and international phone sentinels, redaction masks both."""
    korean_phone = _runtime_value("010", "-", "1234", "-", "5678")
    international_phone = _runtime_value("+82", " ", "10", " ", "9876", " ", "5432")
    value = f"문의 {korean_phone} 또는 {international_phone}"

    redacted = redact_text(value)

    assert korean_phone not in redacted
    assert international_phone not in redacted
    assert redacted.count("[REDACTED_PHONE]") == 2


@pytest.mark.parametrize(
    "phone",
    [
        "02-1234-5678",
        "031 123 4567",
        "(02) 1234-5678",
        "(031)1234 5678",
        "1588-1234",
        "1800 1234",
        "080-123-4567",
        "070 1234 5678",
    ],
)
def test_common_korean_landline_and_service_forms_are_redacted(phone: str) -> None:
    """Given a common Korean phone form, redaction masks the complete value."""
    value = f"문의처 {phone}"

    redacted = redact_text(value)

    assert phone not in redacted
    assert redacted.endswith("[REDACTED_PHONE]")


def test_dates_and_ordinary_numbers_are_not_redacted_as_phones() -> None:
    """Given date and ordinary-number text, phone redaction leaves it unchanged."""
    value = "2026-07-04, 20260704, 1234-5678, 100 000원"

    assert redact_text(value) == value


def test_resident_registration_number_is_redacted_in_memory() -> None:
    """Given an RRN-shaped sentinel, redaction masks it."""
    resident_id = _runtime_value("900101", "-", "1234567")
    value = f"식별값 {resident_id}"

    redacted = redact_text(value)

    assert resident_id not in redacted
    assert "[REDACTED_RESIDENT_ID]" in redacted


def test_labelled_identifier_is_redacted_in_memory() -> None:
    """Given labelled student/staff/account values, redaction masks their values."""
    student_id = _runtime_value("2026", "1234")
    staff_id = _runtime_value("12345", "67890")
    account = _runtime_value("110123", "456789")
    value = f"학번: {student_id} 사번 {staff_id} 계좌 {account}"

    redacted = redact_text(value)

    assert student_id not in redacted
    assert staff_id not in redacted
    assert account not in redacted
    assert redacted.count("[REDACTED_IDENTIFIER]") == 3


def test_unlabelled_account_like_run_is_redacted_in_memory() -> None:
    """Given an unlabelled account-like run, v1 masks the run."""
    account_like = _runtime_value("123456", "789012")
    value = f"송금 참조값 {account_like}"

    redacted = redact_text(value)

    assert account_like not in redacted
    assert "[REDACTED_ACCOUNT]" in redacted


def test_dates_amounts_and_notice_numbers_are_preserved() -> None:
    """Given ordinary date, amount, and notice-number text, redaction preserves it."""
    value = "2026.07.04, 100,000원, 공지번호 2026-1234"

    redacted = redact_text(value)

    assert redacted == value


def test_long_notice_number_is_preserved_when_explicitly_labelled() -> None:
    """Given a labelled notice number, v1 avoids an account false positive."""
    value = "공지번호 2026123456"

    assert redact_text(value) == value


def test_all_human_fields_are_redacted_and_capped() -> None:
    """Given oversized fields, every human-authored value is redacted and bounded."""
    fields = redact_human_fields(
        title="T" * 700,
        category_name="일반" * 100,
        department="D" * 300,
        body="B" * 5000,
    )

    assert REDACTION_VERSION == "v1"
    assert len(fields.title) == 500
    assert len(fields.category_name) == 64
    assert len(fields.department) == 200
    assert len(fields.body) == 4000


def test_persisted_human_fields_never_store_complete_sentinels(tmp_path: Path) -> None:
    """Given sensitive detail fields, SQLite stores only redacted text."""
    title = _runtime_value("title sentinel private.person", "@", "example.invalid")
    department = "department sentinel 02-1234-5678"
    body = "body sentinel 1588-1234"
    database = tmp_path / "notices.sqlite3"
    notice = NoticeDetail(
        summary=NoticeSummary(
            duid=DUID("1001"),
            title=title,
            category_id=CategoryId("general"),
            category_name=CategoryName("일반"),
            posted_date=date(2026, 7, 1),
            updated_date=date(2026, 7, 1),
            department=department,
            source_url=SourceURL(
                "https://www.kw.ac.kr/ko/life/notice.jsp?BoardMode=view&DUID=1001"
            ),
            attachments_present=False,
            pinned=False,
        ),
        body=body,
    )

    with open_storage(database) as store:
        _ = store.save_detail(notice)

    database_bytes = database.read_bytes()

    assert title.encode() not in database_bytes
    assert department.encode() not in database_bytes
    assert body.encode() not in database_bytes
