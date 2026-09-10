"""Tests for textbook TOC → spine normalization (deterministic import)."""

from __future__ import annotations

import pytest

from deeptutor.book.importer import MAX_CHAPTERS, TocImportError, toc_to_spine
from deeptutor.book.models import ContentType


def test_nested_toc_flattens_in_dfs_order() -> None:
    toc = [
        {"title": "第一课 社会主义从空想到科学、从理论到实践的发展"},
        {
            "title": "第二课 只有社会主义才能救中国",
            "children": [{"title": "第一框 新民主主义革命的胜利"}],
        },
        {"title": "综合探究", "content_type": "practice"},
    ]
    spine = toc_to_spine("bk_test", toc)

    assert [c.title for c in spine.chapters] == [
        "第一课 社会主义从空想到科学、从理论到实践的发展",
        "第二课 只有社会主义才能救中国",
        "第一框 新民主主义革命的胜利",
        "综合探究",
    ]
    assert [c.order for c in spine.chapters] == [0, 1, 2, 3]
    assert all(c.id for c in spine.chapters)
    assert all(c.page_ids == [] for c in spine.chapters)
    assert spine.chapters[3].content_type == ContentType.PRACTICE


def test_unknown_content_type_falls_back_to_theory() -> None:
    spine = toc_to_spine("bk_test", [{"title": "课", "content_type": "nope"}])
    assert spine.chapters[0].content_type == ContentType.THEORY


def test_empty_title_rejected() -> None:
    with pytest.raises(TocImportError):
        toc_to_spine("bk_test", [{"title": "  "}])
    with pytest.raises(TocImportError):
        toc_to_spine("bk_test", [{"title": "课", "children": [{"title": ""}]}])


def test_empty_toc_rejected() -> None:
    with pytest.raises(TocImportError):
        toc_to_spine("bk_test", [])


def test_non_dict_node_rejected() -> None:
    with pytest.raises(TocImportError):
        toc_to_spine("bk_test", ["第一课"])  # type: ignore[list-item]


def test_chapter_budget_enforced() -> None:
    toc = [{"title": f"第{i}课"} for i in range(MAX_CHAPTERS + 1)]
    with pytest.raises(TocImportError):
        toc_to_spine("bk_test", toc)


def test_summary_and_objectives_carried_through() -> None:
    spine = toc_to_spine(
        "bk_test",
        [
            {
                "title": "第一课",
                "summary": "社会主义发展史",
                "learning_objectives": ["理解空想社会主义的局限"],
            }
        ],
    )
    assert spine.chapters[0].summary == "社会主义发展史"
    assert spine.chapters[0].learning_objectives == ["理解空想社会主义的局限"]


def test_layout_to_spine_rebuilds_from_running_headers() -> None:
    from deeptutor.book.importer import layout_to_spine

    layout = {
        "pdf_info": [
            {"page_idx": 0, "para_blocks": [], "discarded_blocks": [
                {"type": "footer", "lines": [{"spans": [{"content": "第一课 社会主义从空想到科学、从理论到实践的发展"}]}]},
                {"type": "page_number", "lines": [{"spans": [{"content": "1"}]}]},
            ]},
            {"page_idx": 19, "para_blocks": [], "discarded_blocks": [
                {"type": "footer", "lines": [{"spans": [{"content": "第二课 只有社会主义才能救中国"}]}]},
                {"type": "page_number", "lines": [{"spans": [{"content": "15"}]}]},
            ]},
        ]
    }
    spine = layout_to_spine("bk_layout", layout)
    assert [c.title for c in spine.chapters] == [
        "第一课 社会主义从空想到科学、从理论到实践的发展",
        "第二课 只有社会主义才能救中国",
    ]
    assert spine.chapters[0].meta["printed_page"] == 1
    assert spine.chapters[1].meta["printed_page"] == 15


def test_layout_to_spine_rejects_empty_layout() -> None:
    import pytest

    from deeptutor.book.importer import layout_to_spine
    with pytest.raises(Exception):
        layout_to_spine("bk_layout", {"pdf_info": []})
