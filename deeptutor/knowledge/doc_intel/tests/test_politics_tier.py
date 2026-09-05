"""政治档判级（低层级书适配, P5）。

政治教材 MinerU 常把全书标题压平在 text_level 2（封面 lvl1，单元/课/目全部
lvl2），B2-a 三层判据全不命中。政治档按"课制形态 + 目录反查"判级：第N单元
→ 2，第N课 → 3，其余 lvl2 短块过栏目黑名单 + 目录页反查 → 目 (4)。
"""

from __future__ import annotations

from deeptutor.knowledge.doc_intel.structure import (
    _is_politics_book,
    build_tree,
    stable_node_id,
)


def _flatten(tree: dict, acc: list | None = None) -> list[dict]:
    acc = acc if acc is not None else []
    for child in tree.get("children", []):
        acc.append(child)
        _flatten(child, acc)
    return acc


def mus_of(tree: dict) -> list[dict]:
    return [n for n in _flatten(tree) if n.get("type") == "mu"]


def _text(level: int | None, text: str, page: int = 0) -> dict:
    b = {"type": "text", "text": text, "page_idx": page}
    if level is not None:
        b["text_level"] = level
    return b


# 形态复刻必修3 实锤：封面 lvl1，目录块多行"目名+页码"，单元/课/目全 lvl2。
POLITICS_V1_BOOK = [
    _text(1, "思想 政治"),
    _text(None, "必修3"),
    _text(1, "政治与法治"),
    _text(None, "人民教育出版社"),
    _text(2, "目录"),
    _text(
        None,
        "第一单元 中国共产党的领导 1\n"
        "第一课 历史和人民的选择 2\n"
        "中华人民共和国成立前各种政治力量 2\n"
        "中国共产党领导人民站起来、富起来、强起来 8\n"
        "第二课 中国共产党的先进性 15\n"
        "始终坚持以人民为中心 15\n"
        "第三课 坚持和加强党的全面领导 25\n"
        "坚持党的领导 25",
        page=1,
    ),
    _text(2, "第一单元 中国共产党的领导", page=2),
    _text(None, "中华民族是世界上伟大的民族……", page=2),
    _text(2, "第一课历史和人民的选择", page=2),
    _text(2, "中华人民共和国成立前各种政治力量", page=2),
    _text(None, "1919—1949年是中国的新民主主义革命时期……", page=3),
    _text(2, "探究与分享", page=3),
    _text(2, "半殖民地 半封建", page=3),
    _text(2, "◆◆◆ 名词点击", page=3),
    _text(2, "没有共产党就没有新中国", page=4),
    _text(2, "中国共产党领导人民站起来、富起来、强起来", page=5),
    _text(2, "第二课 中国共产党的先进性", page=6),
    _text(2, "始终坚持以人民为中心", page=6),
    _text(2, "学思之窗", page=7),
    _text(2, "人民教育出版社", page=7),
    _text(2, "第三课 坚持和加强党的全面领导", page=8),
    _text(2, "坚持党的领导", page=8),
    _text(2, "相关链接", page=9),
]


def test_politics_tier_trigger_conditions():
    """触发：max(text_level)≤2 且 lvl2 中 第N课 标题 ≥3；否则不触发。"""
    assert _is_politics_book(POLITICS_V1_BOOK, lambda b: b.get("text") or "")
    # 少于 3 个 lvl2 课标题 → 不触发
    assert not _is_politics_book(POLITICS_V1_BOOK[:16], lambda b: b.get("text") or "")
    # 有 text_level 3（数学/语文书的层级未压平）→ 不触发
    leveled = POLITICS_V1_BOOK + [_text(3, "人民代表大会制度的优势", page=20)]
    assert not _is_politics_book(leveled, lambda b: b.get("text") or "")
    # 只有 3 个 lvl2 课标题但无目录 → 触发档仍开，但目候选全被反查拒绝
    toc_less = [b for b in POLITICS_V1_BOOK if b.get("text") != "目录" and "1\n" not in (b.get("text") or "")]
    assert _is_politics_book(toc_less, lambda b: b.get("text") or "")
    tree, _ = build_tree(toc_less, doc_id="p0")
    assert mus_of(tree) == []


def test_politics_tier_levels_unit_lesson_mu():
    """单元→2，课→3，目录反查过的目→4 (type=mu)，层级挂接正确。"""
    tree, paths = build_tree(POLITICS_V1_BOOK, doc_id="p1")
    assert tree is not None
    assert tree["title"] == "政治与法治"  # 封面 lvl1 收作书名
    units = [n for n in tree["children"]]
    assert [u["title"] for u in units] == ["第一单元 中国共产党的领导"]
    assert all(u["level"] == 2 and "type" not in u for u in units)
    lessons = units[0]["children"]
    assert [l["title"] for l in lessons] == [
        "第一课历史和人民的选择",
        "第二课 中国共产党的先进性",
        "第三课 坚持和加强党的全面领导",
    ]
    assert all(l["level"] == 3 and "type" not in l for l in lessons)
    mus = mus_of(tree)
    assert [m["title"] for m in mus] == [
        "中华人民共和国成立前各种政治力量",
        "中国共产党领导人民站起来、富起来、强起来",
        "始终坚持以人民为中心",
        "坚持党的领导",
    ]
    assert all(m["level"] == 4 for m in mus)
    # node_id 公式不变（沿用 stable_node_id）。
    for m in mus:
        assert m["node_id"] == stable_node_id("p1", m["struct_path"])
    # 目下的正文块继承含目名的路径。
    assert paths[10].endswith("/第一课历史和人民的选择/中华人民共和国成立前各种政治力量")


def test_politics_mu_filters_blacklist_and_toc_reverse_lookup():
    """目候选两道过滤：栏目黑名单 + 目录反查（非目录目名一律不收）。"""
    tree, paths = build_tree(POLITICS_V1_BOOK, doc_id="p2")
    titles = {m["title"] for m in mus_of(tree)}
    # 栏目黑名单（含带装饰符与政治档扩展栏目）
    for noise in ("探究与分享", "学思之窗", "相关链接", "名词点击"):
        assert noise not in titles
    # 出版页与正文小标题：目录反查不命中
    for noise in ("人民教育出版社", "没有共产党就没有新中国", "半殖民地 半封建"):
        assert noise not in titles
    # 被拒块不产生结构路径。
    body_paths = [p for p in paths if p]
    assert not any("探究与分享" in p for p in body_paths)
    assert not any("人民教育出版社" in p for p in body_paths)
    assert not any("没有共产党就没有新中国" in p for p in body_paths)


def test_math_book_does_not_trigger_politics_tier():
    """数学样书判据不受影响：章/节/三段编号书不进政治档，走原判据。"""
    math_book = [
        _text(1, "第六章 计数原理"),
        _text(2, "6.2 排列与组合"),
        _text(2, "6.2.1 排列"),
        _text(None, "排列的定义……"),
    ]
    assert not _is_politics_book(math_book, lambda b: b.get("text") or "")
    tree, paths = build_tree(math_book, doc_id="m1")
    mus = mus_of(tree)
    assert [m["title"] for m in mus] == ["6.2.1 排列"]
    assert mus[0]["struct_path"] == "第六章 计数原理/6.2 排列与组合/6.2.1 排列"
    assert paths[3] == mus[0]["struct_path"]


def test_politics_toc_page_never_opens_structure():
    """目录页行（目名+页码）不直接开结构，正文第一单元负责出目录。"""
    tree, _ = build_tree(POLITICS_V1_BOOK, doc_id="p3")
    paths_seen = []

    def walk(nodes):
        for n in nodes:
            paths_seen.append(n["title"])
            walk(n.get("children", []))

    walk(tree["children"])
    # 树里只有一单元一节：目录页的"第二课/第三课"行没有抢先开课。
    assert paths_seen.count("第一单元 中国共产党的领导") == 1
