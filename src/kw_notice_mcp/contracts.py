"""Todo 3 storage seam fixed by the Todo 2 domain boundary."""

from typing import Protocol

from kw_notice_mcp.domain import NoticeDetail, NoticeSummary


class NoticeStore(Protocol):
    """Storage capability that accepts only parsed, redacted domain records."""

    def save_summary(self, notice: NoticeSummary) -> None:
        """Persist one redacted list record keyed by its DUID."""

    def save_detail(self, notice: NoticeDetail) -> None:
        """Persist one redacted detail record and its bounded body."""
