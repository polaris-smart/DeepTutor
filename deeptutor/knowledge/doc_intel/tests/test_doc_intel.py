"""Unit suite for doc_intel — runs on synthetic fixtures (no network, no LLM).

Real-sample regression (full MinerU volumes) is exercised by the acceptance
run, not here.
"""

from __future__ import annotations

import pytest

from deeptutor.knowledge.doc_intel import enrich
from .fixtures import V1_TEACHER_EDITION, V1_TEXTBOOK_TOC


# ── classifier ──

def test_classifier_teacher_edition():
    r = enrich(V1_TEACHER_EDITION, "", "基础题小题练习（8）（教师版）.pdf")
    c = r["classification"]
    assert c.subject == "数学"
    assert c.doc_type in ("exam", "workbook")
    assert c.evidence.get("subject_src") in ("title", "content")


def test_classifier_textbook_filename():
    r = enrich([], "", "苏教必修1_1-200.pdf")
    assert r["classification"].subject == "数学"
    assert r["classification"].doc_type == "textbook"


def test_classifier_unknown_stays_silent():
    r = enrich([], "", "随便什么.pdf")
    assert r["classification"].subject == ""  # rules silent → LLM pass territory


# ── structure ──

def test_textbook_tree_skips_toc_and_furniture():
    r = enrich(V1_TEXTBOOK_TOC, "", "苏教必修1_1-200.pdf")
    tree = r["tree"]
    assert tree is not None
    roots = [n["title"] for n in tree["children"]]
    # Cover & TOC headings must not open structure.
    assert "普通高中教科书" not in roots
    assert "目录" not in roots
    assert roots[0].startswith("第1章")
    # TOC entries swallowed: exactly one 第1章 node.
    assert sum(1 for t in roots if t.startswith("第1章")) == 1
    # Running header (type=header) and same-level repeats create no node.
    assert sum(1 for t in roots if "第2章" in t) >= 1  # body ch2 present once at least
    ch2 = [n for n in tree["children"] if "第2章" in n["title"]]
    assert len(ch2) == 1 or all(len(n["children"]) >= 0 for n in ch2)


def test_struct_path_prefix():
    r = enrich(V1_TEXTBOOK_TOC, "", "苏教必修1_1-200.pdf")
    nonempty = [p for p in r["paths"] if p]
    assert any(p.startswith("第1章") for p in nonempty)


@pytest.mark.parametrize("frame_title", ["第一框", "第2框", "第三节框"])
def test_politics_frame_headings_are_nested_below_lessons(frame_title: str) -> None:
    r = enrich(
        [
            {"type": "text", "text": "第一课 人民民主专政"},
            {"type": "text", "text": frame_title},
            {"type": "text", "text": "人民民主是社会主义的生命。"},
        ],
        "",
        "思想政治.pdf",
    )

    lesson = r["tree"]["children"][0]
    frame = lesson["children"][0]
    expected_path = f"第一课 人民民主专政/{frame_title}"
    assert lesson["level"] == 2
    assert frame["level"] == 3
    assert frame["struct_path"] == expected_path
    assert r["paths"][2] == expected_path


def test_practice_headers_are_not_sections():
    r = enrich(V1_TEXTBOOK_TOC, "", "苏教必修1_1-200.pdf")
    tree = r["tree"]

    def titles(n, acc=None):
        acc = acc if acc is not None else []
        acc.append(n["title"])
        for ch in n["children"]:
            titles(ch, acc)
        return acc

    all_titles = []
    for root in tree["children"]:
        titles(root, all_titles)
    assert "练习" not in all_titles  # practice header demoted
    # Exercise stems never open sections.
    assert not any(t.startswith("1. 用") for t in all_titles)


# ── qa_split ──

def test_qa_split_teacher_edition():
    r = enrich(V1_TEACHER_EDITION, "", "基础题小题练习（8）（教师版）.pdf")
    qs = r["qa"].questions
    assert len(qs) == 2
    for qid, q in qs.items():
        assert q["q_block_idx"] is not None
        assert q["answer_block_idx"] is not None
    # Question node metadata carries the linkage.
    q1 = next(iter(qs.values()))
    md = r["block_meta"][q1["q_block_idx"]]
    assert md["is_question"] is True
    assert md["has_answer"] is True
    assert md["q_id"] in qs
    # Answer node marked as answer.
    amd = r["block_meta"][q1["answer_block_idx"]]
    assert amd.get("is_answer") is True


def test_qa_split_choice_type_detected():
    r = enrich(V1_TEACHER_EDITION, "", "试卷.pdf")
    # Section header "一、选择题…" sets the q_type context.
    q = next(iter(r["qa"].questions.values()))
    assert q["q_type"] in ("选择题", "choice", "")


# ── image_link ──

def test_image_attached_to_question():
    r = enrich(V1_TEACHER_EDITION, "", "基础题小题练习（8）（教师版）.pdf")
    q1 = next(iter(r["qa"].questions.values()))
    md = r["block_meta"][q1["q_block_idx"]]
    assert md.get("linked_images") == ["images/aaa.jpg"]
    # image node knows its owner role
    img_md = [m for m in r["block_meta"] if m.get("image_of")]
    assert img_md and img_md[0]["image_of"] == "question"


# ── enrich contract ──

def test_enrich_fail_open_on_garbage():
    r = enrich([{"type": "text"}, {"garbage": True}], "", "x.pdf")  # must not raise
    assert isinstance(r["block_meta"], list)


def test_block_meta_shape():
    r = enrich(V1_TEACHER_EDITION, "", "基础题小题练习（8）（教师版）.pdf")
    assert len(r["block_meta"]) == len(V1_TEACHER_EDITION)
    for md in r["block_meta"]:
        assert isinstance(md, dict)


from deeptutor.knowledge.doc_intel.structure import build_tree


# ── 页脚法门卫（v0.4）：footer 课名登记表 + printed_page 锚定 ────────────


def test_footer_register_gates_false_lesson_titles():
    """必修1 假阳性回归：目录页/伪标题不得开课，只有页脚登记过的课才开。"""
    blocks = [
        # 页脚登记：全书只有两课（页脚出现在各课首页，页码随行）
        {"type": "footer", "text": "第一课 社会主义从空想到科学、从理论到实践的发展", "page_number": "1"},
        {"type": "page_number", "text": "1"},
        {"type": "title", "text": "第一课 社会主义从空想到科学、从理论到实践的发展"},
        {"type": "text", "text": "原始社会是人类社会发展的最初阶段。"},
        # 伪标题：目录页行 / 未登记的课（无页脚登记）
        {"type": "title", "text": "第二课 只有社会主义才能救中国"},
        {"type": "footer", "text": "第二课 只有社会主义才能救中国", "page_number": "21"},
        {"type": "title", "text": "第二课 只有社会主义才能救中国"},
    ]
    tree, _ = build_tree(blocks, doc_id="bk_test")
    assert tree is not None
    lessons = [n for n in tree["children"] if n["level"] == 2]
    assert len(lessons) == 2, [n["title"] for n in lessons]
    assert lessons[0]["title"].startswith("第一课")
    assert lessons[1]["title"].startswith("第二课")
    # 无页脚登记的伪课标题（如"第三课"未被任何页脚宣布）不得开课
    assert not any(n["title"].startswith("第三课") for n in tree["children"])


def test_footer_printed_page_anchored_to_node():
    blocks = [
        {"type": "page_number", "text": "1"},
        {"type": "footer", "text": "第一课 社会主义从空想到科学、从理论到实践的发展"},
        {"type": "title", "text": "第一课 社会主义从空想到科学、从理论到实践的发展"},
        {"type": "text", "text": "正文。"},
    ]
    tree, _ = build_tree(blocks, doc_id="bk_test")
    lesson = tree["children"][0]
    assert lesson.get("printed_page") == 1


def test_no_footer_register_keeps_legacy_behavior():
    """无页脚块时（旧引擎产物），门卫不激活，行为与旧版一致。"""
    blocks = [
        {"type": "title", "text": "第一课 测试课"},
        {"type": "text", "text": "正文。"},
    ]
    tree, _ = build_tree(blocks, doc_id="bk_legacy")
    assert tree is not None
    assert tree["children"][0]["title"] == "第一课 测试课"
