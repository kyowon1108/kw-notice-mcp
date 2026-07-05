"""Regression tests for notice titles polluted by non-text anchor nodes."""

from pathlib import Path

from kw_notice_mcp.parser import parse_detail_html, parse_list_html
from kw_notice_mcp.values import DUID

FIXTURES = Path(__file__).parents[1] / "fixtures"
TITLE_NOISE = ("비밀글", "뉴아이콘", "첨부", "신규게시글")


def test_titles_drop_icon_and_screen_reader_text() -> None:
    """Given comment-marked icon slots, parsed titles keep only the notice text."""
    html = (FIXTURES / "board_list_live_shape_sanitized.html").read_text(
        encoding="utf-8"
    )

    result = parse_list_html(html)

    by_duid = {record.duid: record.title for record in result.records}
    assert by_duid[DUID("900003")] == "합성 점검 공지 C"
    assert by_duid[DUID("900004")] == "합성 학생 공지 D"
    assert tuple(by_duid.values()) == (
        "합성 고정 공지 A",
        "합성 일반 공지 B",
        "합성 점검 공지 C",
        "합성 학생 공지 D",
    )
    # The live list marks icon slots as HTML comments inside the anchor. Comment
    # subclasses NavigableString, so a string=True search once pulled all three
    # of these fragments into every stored title.
    assert not any(
        noise in title for title in by_duid.values() for noise in TITLE_NOISE
    )


def test_icon_span_still_marks_attachments_after_title_extraction() -> None:
    """Given an icon span the title ignores, the attachment probe still sees it."""
    html = (FIXTURES / "board_list_live_shape_sanitized.html").read_text(
        encoding="utf-8"
    )

    result = parse_list_html(html)

    assert result.records[2].attachments_present is False
    assert result.records[3].attachments_present is True


def test_list_title_ignores_comments_between_category_and_text() -> None:
    """Given comments wrapping the anchor text, only the visible text is kept."""
    html = """
    <section class="board-list-box">
      <ul>
        <li>
          <a href="/ko/life/notice.jsp?BoardMode=view&DUID=900101">
            <strong class="category">[일반]</strong>
            <!-- 비밀글일 경우 비밀글 아이콘 표시 -->
            합성 폴백 공지
            <!-- 뉴아이콘 -->
            <span class="ico-new">신규게시글</span>
            <!-- 첨부-->
          </a>
          <p class="info">
            조회수 1 | 작성일 2026.08.01 | 수정일 2026.08.01 | 합성부서
          </p>
        </li>
      </ul>
    </section>
    """

    result = parse_list_html(html)

    assert result.issues == ()
    assert tuple(record.title for record in result.records) == ("합성 폴백 공지",)


def test_detail_title_ignores_comment_nodes() -> None:
    """Given comments inside the detail title, only the visible text is kept."""
    html = """
    <article>
      <h1 class="title">
        합성 상세 공지
        <!-- 비밀글일 경우 비밀글 아이콘 표시 -->
        <!-- 뉴아이콘 -->
        <!-- 첨부-->
      </h1>
      <span class="category">일반</span>
      <span class="posted-date">2026.08.04</span>
    </article>
    """

    result = parse_detail_html(html, "1007")

    assert result.notice is not None
    assert result.notice.summary.title == "합성 상세 공지"
