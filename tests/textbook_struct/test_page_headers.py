"""Running-header chapter rebuild: footer (人教版) + header (苏教版) both.

09-11 生产实锤：苏教版教材章名印在**页眉**（“集合第1章”/“空间向量与立体
几何 第6章”），页脚只有出版社名——纯 footer 页脚法 0 章命中。扩展后
header 中嵌章 token 的形态被抽取并规整为“第N章 章名”，与 footer 路径
共用 level 过滤与 seen 去重。
"""

from __future__ import annotations

from deeptutor.textbook_struct.chapter_rebuild import rebuild_from_headers_level
from deeptutor.textbook_struct.page_headers import (
    normalize_header_chapter,
    page_facts,
)


def _page(page_idx: int, discarded: list[dict]) -> dict:
    return {"page_idx": page_idx, "discarded_blocks": discarded}


def _footer(text: str) -> dict:
    return {"type": "footer", "lines": [{"spans": [{"content": text}]}]}


def _header(text: str) -> dict:
    return {"type": "header", "lines": [{"spans": [{"content": text}]}]}


def _page_number(n: str) -> dict:
    return {"type": "page_number", "lines": [{"spans": [{"content": n}]}]}


# ── normalize：苏教页眉三形态 + 防误切 ───────────────────────────────────


def test_normalize_embedded_chapter_name_before_token() -> None:
    assert normalize_header_chapter("集合第1章") == "第1章 集合"


def test_normalize_embedded_chapter_name_after_token() -> None:
    assert normalize_header_chapter("空间向量与立体几何 第6章") == "第6章 空间向量与立体几何"


def test_normalize_bare_token() -> None:
    assert normalize_header_chapter("第6章") == "第6章"


def test_normalize_leading_token_keeps_footer_shape() -> None:
    assert normalize_header_chapter("第6章 空间向量与立体几何") == "第6章 空间向量与立体几何"


def test_normalize_rejects_book_title_and_prose() -> None:
    # 书名年份、无章 token 的普通页眉、长正文都不是章界。
    assert normalize_header_chapter("高中数学人教A版2019") is None
    assert normalize_header_chapter("数学·必修第一册") is None
    assert normalize_header_chapter("a" * 60) is None


# ── page_facts：双通道采集 ────────────────────────────────────────────────


def test_page_facts_reads_footer_verbatim() -> None:
    footers, printed = page_facts(
        _page(3, [_footer("第一章 集合与常用逻辑用语"), _page_number("12")])
    )
    assert footers == ["第一章 集合与常用逻辑用语"]
    assert printed == "12"


def test_page_facts_reads_header_normalized() -> None:
    footers, printed = page_facts(_page(4, [_header("集合第1章"), _page_number("13")]))
    assert footers == ["第1章 集合"]
    assert printed == "13"


# ── rebuild_from_headers_level：页眉驱动建章端到端 ─────────────────────────


def _sujiao_layout() -> dict:
    """苏教形态：章名在页眉，两章各自页起（printed 页码供偏移校验）。

    物理页(1-based) − 印刷页码 恒为 1（封面不印页码，正文从印刷 2 起）。
    """
    return {
        "pdf_info": [
            _page(0, [_header("高中数学·必修第一册"), _page_number("1")]),  # 封面
            _page(1, [_header("集合与常用逻辑用语 第1章"), _page_number("2")]),
            _page(2, [_header("集合与常用逻辑用语 第1章"), _page_number("3")]),
            _page(19, [_header("一元二次函数、方程和不等式 第2章"), _page_number("20")]),
        ]
    }


def test_rebuild_level_builds_chapters_from_headers() -> None:
    chapters = rebuild_from_headers_level(_sujiao_layout(), unit="章")
    assert [c.title for c in chapters] == [
        "第1章 集合与常用逻辑用语",
        "第2章 一元二次函数、方程和不等式",
    ]
    assert [c.page_idx for c in chapters] == [1, 19]
    assert chapters[0].meta["printed_page"] == 2
    assert chapters[1].meta["printed_page"] == 20
    # 偏移恒定：物理页 − 印刷页 = 1，全书一致。
    assert verify_offsets_ok(chapters)


def verify_offsets_ok(chapters) -> bool:
    from deeptutor.textbook_struct.chapter_rebuild import verify_offset

    return verify_offset(chapters)["ok"]


def test_rebuild_level_unit_filter_drops_other_levels() -> None:
    # 双级页眉（“第X章/第Y节”轮换）时，unit=节 只认节级变化。
    layout = {
        "pdf_info": [
            _page(0, [_header("函数 第2章"), _header("函数的概念 第1节"), _page_number("5")]),
            _page(1, [_header("函数 第2章"), _header("函数的表示法 第2节"), _page_number("9")]),
        ]
    }
    chapters = rebuild_from_headers_level(layout, unit="节")
    assert [c.title for c in chapters] == ["第1节 函数的概念", "第2节 函数的表示法"]
