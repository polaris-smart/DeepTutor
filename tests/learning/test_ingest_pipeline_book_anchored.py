"""ingest_pipeline 书锚定改造的行为测试（structured / qb_generated 两段）.

覆盖任务书要求的四个点：
① 出题章节锚定书本身的 spine 章节链（章数=书章数，不是 KB 叶数）
② 出题 prompt 携带该书各章页正文（reading 块原文）
③ 书没有 canonical 树时 fail-loud（不静默回退 KB 全树）
④ 每章页正文 6000 字符预算截断、按页序保留前面的页
另覆盖 ``_default_load_book`` 对真实书仓（canonical 树 + spine + reading 页）的读取。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from deeptutor.learning import ingest_pipeline as ip

# ---------------------------------------------------------------------------
# 测试脚手架
# ---------------------------------------------------------------------------


def _canonical_tree() -> dict[str, Any]:
    """书自己的 canonical 树（12 个目级节点，均带 node_id）。"""
    return {
        "title": "普通高中教科书 数学 必修第一册",
        "children": [{"title": f"目{i}", "node_id": f"n1-{i}", "children": []} for i in range(12)],
    }


def _spine_chapters() -> list[dict[str, str]]:
    return [
        {"id": "ch-1", "title": "第一章 集合与常用逻辑用语"},
        {"id": "ch-2", "title": "第二章 一元二次函数、方程和不等式"},
        {"id": "ch-3", "title": "第三章 函数的概念与性质"},
    ]


def _fake_book(tree: Any = "default") -> dict[str, Any]:
    return {
        "canonical_tree": _canonical_tree() if tree == "default" else tree,
        "chapters": _spine_chapters(),
        "pages": {
            "ch-1": [
                {
                    "page_id": "pg-1",
                    "title": "第1页",
                    "text": "教材原文：集合 A 的子集个数公式。例题：已知 A={1,2}，求 A 的子集个数。答案：4 个。解析：子集个数为 2^n。",
                },
                {
                    "page_id": "pg-2",
                    "title": "第2页",
                    "text": "习题：判断 {1} 与 {1,2} 的关系。答案：{1} 是 {1,2} 的真子集。解析：元素与集合用属于，集合与集合用包含。",
                },
            ],
            "ch-2": [
                {
                    "page_id": "pg-3",
                    "title": "第3页",
                    "text": "例题：解不等式 x^2-3x+2>0。答案：(−∞,1)∪(2,+∞)。解析：因式分解后穿根。",
                }
            ],
            "ch-3": [],
        },
    }


def _llm_questions_payload() -> str:
    """一段合法的 LLM 出题输出（5 题，含 A-D 选项与答案）。"""
    return json.dumps(
        [
            {
                "question": f"题目{i}",
                "options": {"A": "甲", "B": "乙", "C": "丙", "D": "丁"},
                "answer": "A",
                "explanation": "出处：第1页。解析内容",
                "difficulty": "巩固",
            }
            for i in range(5)
        ],
        ensure_ascii=False,
    )


def _deps(
    tmp_path: Path,
    *,
    book: Any,
    kb_tree: Any = None,
    prompts: list[str] | None = None,
) -> ip.PipelineDeps:
    captured = prompts if prompts is not None else []

    def llm_complete(messages: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        captured.append(messages[0]["content"])
        return _llm_questions_payload(), {"total_tokens": 100, "model": "mock-llm"}

    return ip.PipelineDeps(
        load_tree=lambda kb_name: kb_tree,
        load_book=lambda book_id: book,
        llm_complete=llm_complete,
        qbanks_dir=lambda: tmp_path / "qbanks",
    )


def _record() -> dict[str, Any]:
    return {"book_id": "bk-1", "kb_name": "merged-kb"}


# ---------------------------------------------------------------------------
# ① 书锚定：章节数 = 书章数（spine），不是 KB 叶数
# ---------------------------------------------------------------------------


def test_structured_anchors_chapters_to_book_spine_not_kb(tmp_path: Path) -> None:
    kb_tree = {
        "title": "merged KB",
        "children": [
            {"title": f"KB叶子{i}", "node_id": f"kb-{i}", "children": []} for i in range(50)
        ],
    }
    ctx: dict[str, Any] = {}
    result = ip._stage_structured(
        _record(), ctx, _deps(tmp_path, book=_fake_book(), kb_tree=kb_tree)
    )

    assert [c["id"] for c in ctx["chapters"]] == ["ch-1", "ch-2", "ch-3"]
    assert [c["title"] for c in ctx["chapters"]] == [c["title"] for c in _spine_chapters()]
    assert ctx["tree"] == _canonical_tree()  # 映射树也换成书自己的 canonical 树
    assert result["node_count"] == 12  # canonical 树目级节点数，与 KB 叶数无关
    assert "书锚定" in result["note"]


# ---------------------------------------------------------------------------
# ② prompt 含页正文片段
# ---------------------------------------------------------------------------


def test_qb_generated_prompt_carries_page_reading_text(tmp_path: Path) -> None:
    prompts: list[str] = []
    ctx: dict[str, Any] = {}
    ip._stage_structured(_record(), ctx, _deps(tmp_path, book=_fake_book()))
    result = ip._stage_qb_generated(
        _record(), ctx, _deps(tmp_path, book=_fake_book(), prompts=prompts)
    )

    assert len(prompts) == 3  # 每章一次 LLM 调用
    first = prompts[0]
    assert "集合 A 的子集个数公式" in first  # 第1页 reading 正文进 prompt
    assert "习题：判断 {1} 与 {1,2} 的关系" in first  # 第2页正文进 prompt
    assert "出处：第N页" in first  # prompt 要求标注出处页
    assert "例题" in first and "习题" in first  # 语义：从教材原文提取/编制

    third = prompts[2]
    assert "（该章暂无页正文）" in third  # 无页正文的章有明确占位

    bank_path = tmp_path / "qbanks" / "bk-1.json"
    bank = json.loads(bank_path.read_text(encoding="utf-8"))
    assert len(bank["questions"]) == 15  # 3 章 × QUESTIONS_PER_CHAPTER
    assert {q["chapter_id"] for q in bank["questions"]} == {"ch-1", "ch-2", "ch-3"}
    assert result["count"] == 15


def test_qb_generated_retry_reloads_book_ctx(tmp_path: Path) -> None:
    """断点重跑（单段 retry，ctx 为空）时重新锚定本书，不落回 KB 全树。"""
    prompts: list[str] = []
    result = ip._stage_qb_generated(
        _record(), {}, _deps(tmp_path, book=_fake_book(), prompts=prompts)
    )
    assert result["count"] == 15
    assert "集合 A 的子集个数公式" in prompts[0]


# ---------------------------------------------------------------------------
# ③ 无 canonical 树 fail-loud
# ---------------------------------------------------------------------------


def test_structured_fail_loud_without_canonical_tree(tmp_path: Path) -> None:
    kb_tree = {
        "title": "merged KB",
        "children": [
            {"title": f"KB叶子{i}", "node_id": f"kb-{i}", "children": []} for i in range(50)
        ],
    }
    ctx: dict[str, Any] = {}
    with pytest.raises(RuntimeError, match="canonical"):
        ip._stage_structured(
            _record(),
            ctx,
            _deps(tmp_path, book=_fake_book(tree=None), kb_tree=kb_tree),
        )
    assert "chapters" not in ctx  # 未静默回退 KB 全树


def test_structured_fail_loud_without_spine_chapters(tmp_path: Path) -> None:
    book = {"canonical_tree": _canonical_tree(), "chapters": [], "pages": {}}
    with pytest.raises(RuntimeError, match="spine"):
        ip._stage_structured(_record(), {}, _deps(tmp_path, book=book))


def test_structured_fail_loud_for_missing_book(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="不存在"):
        ip._stage_structured(_record(), {}, _deps(tmp_path, book=None))


# ---------------------------------------------------------------------------
# ④ 预算截断：6000 字符、按页序保留前面
# ---------------------------------------------------------------------------


def test_chapter_source_text_budget_truncates_keeping_earlier_pages() -> None:
    pages = [
        {"page_id": "p1", "title": "第1页", "text": "甲" * 5000},
        {"page_id": "p2", "title": "第2页", "text": "乙" * 5000},
    ]
    out = ip._chapter_source_text(pages)

    assert len(out) <= ip.PROMPT_PAGE_BUDGET
    assert "【第1页】" in out
    assert "甲" * 100 in out  # 前面的页完整保留
    assert "【第2页】" in out
    assert "乙" * 50 in out  # 后面的页保留开头
    assert "乙" * 2000 not in out  # 后面的页超预算部分被截断


def test_chapter_source_text_falls_back_to_page_index_label() -> None:
    pages = [{"page_id": "p1", "title": "", "text": "正文片段"}]
    assert ip._chapter_source_text(pages) == "【第1页】\n正文片段"


# ---------------------------------------------------------------------------
# _default_load_book：真实书仓读取（canonical 树 + spine + reading 页）
# ---------------------------------------------------------------------------


def test_default_load_book_reads_canonical_tree_spine_and_reading_pages(tmp_path: Path) -> None:
    from deeptutor.book.models import (
        Block,
        BlockStatus,
        BlockType,
        Book,
        Chapter,
        Page,
        PageStatus,
        Spine,
    )
    from deeptutor.book.storage import BookStorage
    from deeptutor.services.path_service import PathService

    storage = BookStorage(path_service=PathService(workspace_root=tmp_path / "data"))
    storage.save_book(Book(id="bk-t", title="数学 必修第一册"))
    storage.save_canonical_kp_tree("bk-t", _canonical_tree())
    storage.save_spine(Spine(book_id="bk-t", chapters=[Chapter(id="ch-9", title="第一章 集合")]))
    storage.save_page(
        Page(
            book_id="bk-t",
            chapter_id="ch-9",
            order=0,
            status=PageStatus.READY,
            blocks=[
                Block(
                    type=BlockType.READING,
                    status=BlockStatus.READY,
                    payload={"body": "教材原文片段甲"},
                ),
                Block(
                    type=BlockType.TEXT,
                    status=BlockStatus.READY,
                    payload={"body": "非reading块不应进prompt"},
                ),
            ],
        )
    )

    data = ip._default_load_book("bk-t", storage=storage)
    assert data is not None
    assert isinstance(data["canonical_tree"], dict)
    assert [c["id"] for c in data["chapters"]] == ["ch-9"]
    assert data["pages"]["ch-9"][0]["text"] == "教材原文片段甲"
    assert "非reading块" not in data["pages"]["ch-9"][0]["text"]

    assert ip._default_load_book("bk-missing", storage=storage) is None
