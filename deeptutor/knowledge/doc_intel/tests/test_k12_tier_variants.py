"""K12 多档判级（P6）：历史/地理/语文/英语四档 + 触发互斥。

P5 政治档框架泛化为多档（每档 = 触发条件 + 结构正则 + 目过滤），按书自动
选择。样例形态复刻 parse-sample 实测：历史纲要上（阿拉伯课号课制）、地理
必修一（章节制）、语文必修上（课文标题 lvl 混乱）、译林英语教师用书
（UNIT 制 + 板块行）。
"""

from __future__ import annotations

from deeptutor.knowledge.doc_intel.structure import (
    _is_history_geo_book,
    _is_politics_book,
    _is_yingyu_book,
    _is_yuwen_book,
    _select_flat_tier,
    build_tree,
    stable_node_id,
)


def _flatten(nodes, acc: list | None = None) -> list[dict]:
    """Walk a node list (or a tree dict) depth-first into a flat list."""
    acc = acc if acc is not None else []
    for node in (nodes if isinstance(nodes, list) else (nodes or {}).get("children", [])):
        acc.append(node)
        _flatten(node.get("children", []), acc)
    return acc


def mus_of(tree: dict | None) -> list[dict]:
    if not tree:
        return []
    return [n for n in _flatten(tree["children"]) if n.get("type") == "mu"]


def _text(level: int | None, text: str, page: int = 0) -> dict:
    b = {"type": "text", "text": text, "page_idx": page}
    if level is not None:
        b["text_level"] = level
    return b


def _footer(text: str, page: int = 0) -> dict:
    return {"type": "footer", "text": text, "page_idx": page}


# 形态复刻中外历史纲要（上）实锤：封面 lvl1，目录多行"结构行+页码"，单元/
# 课/目全 ≤lvl2，课号用阿拉伯数字（第1课），第2课标题无空格。
HISTORY_V1_BOOK = [
    _text(1, "历史", page=0),
    _text(1, "中外历史纲要(上)", page=0),
    _text(2, "目录", page=0),
    _text(
        None,
        "第一单元 从中华文明起源到秦汉统一多民族封建国家\n"
        "第1课 中华文明的起源与早期国家 2\n"
        "第2课 诸侯纷争与变法运动 8",
        page=0,
    ),
    _text(2, "第一单元 从中华文明起源到秦汉统一多民族封建国家", page=1),
    _text(None, "中国是远古人类的重要起源地，中华文明是人类最古老的文明之一。", page=1),
    _text(2, "第1课 中华文明的起源与早期国家", page=2),
    _text(2, "学习聚焦", page=2),
    _text(None, "石器时代人类先后以打制和磨制的石器作为工具。", page=2),
    _text(2, "石器时代的古人类和文化遗存", page=2),
    _text(None, "1965年5月，中国地质科学院的研究人员在云南省元谋县……", page=2),
    _text(2, "从部落到国家", page=3),
    _text(2, "学习聚焦", page=3),
    _text(None, "禹建立了中国最早的奴隶制国家。", page=3),
    _text(2, "商和西周", page=3),  # 课内黑体小标题，超出每课 2 目上限
    _text(None, "汤灭夏后建立了商朝。史书中有关商朝的记载……", page=3),
    _text(2, "史料阅读", page=4),
    _text(2, "▲ 战国形势图", page=4),  # 图注
    _text(2, "? 思考点", page=4),  # 栏目
    _text(None, "商朝为什么推行内外服制？", page=4),
    _text(2, "第2课诸侯纷争与变法运动", page=5),  # 无空格课号
    _text(2, "列国纷争与华夏认同", page=5),
    _text(None, "东周分为春秋、战国两个阶段。春秋时期……", page=5),
    _text(2, "第 3 课 秦统一多民族封建国家的建立", page=9),
    _text(None, "秦的统一……", page=9),
    _footer("第1课 中华文明的起源与早期国家", page=3),
    _footer("第2课 诸侯纷争与变法运动", page=5),
    _footer("第3课 秦统一多民族封建国家的建立", page=9),
]

# 形态复刻人教地理必修一实锤：章（第N章）+ 节（第N节，每章重复"第一节"），
# 目=节下 lvl2 短标题；活动/案例/括号子目/编号条目/资料为栏目噪声。
GEO_V1_BOOK = [
    _text(1, "地理", page=0),
    _text(2, "目录", page=0),
    _text(
        None,
        "第一章宇宙中的地球\n第一节 地球的宇宙环境 …… 2\n第二节 太阳对地球的影响 …… 8",
        page=0,
    ),
    _text(2, "第一章 宇宙中的地球", page=1),
    _text(None, "人类的家园——地球，在茫茫宇宙中只是沧海一粟。", page=1),
    _text(2, "第一节 地球的宇宙环境", page=2),
    _text(2, "地球在宇宙中的位置", page=2),
    _text(None, "宇宙大爆炸假说认为……", page=2),
    _text(2, "活动", page=2),
    _text(2, "开展简单的天文现象观测活动", page=2),  # 栏目内容标题（紧邻活动）
    _text(2, "1. 确定月亮位置的方法", page=3),  # 编号活动条目
    _text(2, "图 1.8 月相观测描绘示例", page=3),  # 图注
    _text(2, "(一) 行星地球", page=3),  # 括号子目
    _text(2, "第二节 太阳对地球的影响", page=4),
    _text(2, "太阳辐射对地球的影响", page=4),
    _text(None, "太阳辐射为地球提供光和热……", page=4),
    _text(2, "第三节 地球的历史", page=5),
    _text(2, "化石和地质年代表", page=5),
    _text(None, "地层和化石是记录地球历史的……", page=5),
    _text(2, "第二章 地球上的大气", page=6),
    _text(2, "第一节 大气的组成和垂直分层", page=6),
    _text(2, "大气的组成", page=6),
    _text(None, "大气是由干洁空气、水汽和杂质组成……", page=6),
    _text(2, "资料 2 大气污染及其危害", page=7),  # 图版资料
]

# 形态复刻统编语文必修上册实锤：课文标题 lvl 混乱（None/1），目录课文行
# "1 沁园春·长沙/毛泽东 2"，lvl2 稀疏（<10% 块，书末补正文填充块压低占比）。
_YUWEN_BODY = _text(None, "正文叙述段落，诗歌与小说的正文文本，不构成任何结构标题。", page=2)
YUWEN_V1_BOOK = [
    _text(1, "语文", page=0),
    _text(None, "人民教育出版社", page=0),
    _text(2, "目录", page=0),
    _text(
        None,
        "第一单元\n1 沁园春·长沙/毛泽东 2\n2 立在地球边上放号/郭沫若 4\n"
        "\\* 红烛/闻一多 4\n第二单元\n3 百合花/茹志鹃 30",
        page=0,
    ),
    _text(2, "第一单元", page=1),
    _text(None, "青春是花样年华。怀着美好的梦想……", page=1),
    _YUWEN_BODY,
    _text(None, "沁园春·长沙①", page=2),  # lvl 混乱：课文标题无层级
    _text(None, "毛泽东", page=2),
    _text(None, "独立寒秋，湘江北去，橘子洲头。", page=2),
    _text(None, "学习提示", page=3),
    _text(None, "面对“万类霜天竞自由”的壮丽秋景……", page=3),
    _text(1, "立在地球边上放号①", page=3),  # 课文标题 lvl1
    _text(None, "郭沫若", page=3),
    _text(None, "无数的白云正在空中怒涌……", page=3),
    _text(None, "红烛 $^{③}$", page=4),  # 行内 LaTeX 上标角标
    _text(None, "蜡炬成灰泪始干……", page=4),
    _text(None, "红烛啊", page=4),  # 课文内的诗句行——不收
    _text(2, "第二单元", page=10),
    _YUWEN_BODY,
    _text(1, "百合花①", page=11),
    _text(None, "茹志鹃", page=11),
    _text(None, "一九四六年的中秋……", page=11),
    _text(None, "单元学习任务", page=20),
    _text(None, "研习任务", page=21),
] + [_YUWEN_BODY] + [_text(None, "又一页正文……", page=30) for _ in range(8)]

# 形态复刻译林英语教师用书实锤：UNIT 标题 lvl1，板块行 lvl2（正文裸板块名，
# 附录带前缀"Unit 2 Sports culture Reading"），师用书噪声（Possible answer）。
YINGYU_V1_BOOK = [
    _text(1, "英语", page=0),
    _text(1, "《普通高中教科书·英语 教师教学用书》", page=0),
    _text(2, "目录", page=0),
    _text(
        None,
        "Unit 1 The mass media.... 1\nUnit 2 Sports culture.... 30\n"
        "Unit 3 Fit for life.... 55",
        page=0,
    ),
    _text(1, "The mass media", page=1),
    _text(2, "一、单元概述", page=1),
    _text(None, "本单元教材的主要教学内容与课时安排……", page=1),
    _text(2, "Welcome to the unit", page=2),
    _text(2, "内容分析", page=2),
    _text(None, "板块内容分析……", page=2),
    _text(2, "Reading", page=3),
    _text(2, "Possible answer", page=3),
    _text(None, "（1）B　（2）A", page=3),
    _text(2, "Notes", page=4),
    _text(1, "UNIT 2 Sports culture", page=10),
    _text(2, "Unit 2 Sports culture", page=10),  # 附录复现行（无板块名）——不收
    _text(2, "Grammar and usage", page=11),
    _text(1, "UNIT 3 Fit for life", page=20),
    _text(2, "Project", page=21),
    _text(2, "Appendix II Answer key to Workbook", page=40),
    _text(2, "Unit 2 Sports culture Reading", page=41),  # 附录带前缀板块行
    _text(2, "答案", page=42),
]


# ── 触发互斥（按序匹配：政治 → 历史/地理 → 语文 → 英语）────────────────

def test_tier_triggers_arabic_vs_chinese_lessons_are_disjoint():
    """历史（阿拉伯课号）不入政治档；政治（汉字课号）不入历史/地理档。"""
    assert not _is_politics_book(HISTORY_V1_BOOK, lambda b: b.get("text") or "")
    assert _is_history_geo_book(HISTORY_V1_BOOK, lambda b: b.get("text") or "")
    assert _select_flat_tier(HISTORY_V1_BOOK, lambda b: b.get("text") or "") == "history_geo"
    assert _is_politics_book(POLITICS_LIKE_BOOK, lambda b: b.get("text") or "")
    assert not _is_history_geo_book(POLITICS_LIKE_BOOK, lambda b: b.get("text") or "")
    assert _select_flat_tier(POLITICS_LIKE_BOOK, lambda b: b.get("text") or "") == "politics"


def test_tier_triggers_math_native_book_hits_no_flat_tier():
    """数学原生档：章/节制书四档全不命中，走 B2-a 原生判据。"""
    math_book = [
        _text(1, "第六章 计数原理"),
        _text(2, "6.2 排列与组合"),
        _text(2, "6.2.1 排列"),
        _text(None, "排列的定义……"),
    ]
    text_fn = lambda b: b.get("text") or ""
    assert _select_flat_tier(math_book, text_fn) == ""
    tree, _ = build_tree(math_book, doc_id="m1")
    mus = mus_of(tree)
    assert [m["title"] for m in mus] == ["6.2.1 排列"]


def test_tier_triggers_yuwen_yingyu_disjoint_from_history_geo():
    """语文（lvl2 稀疏 + 目录课文行）与英语（lvl1 UNIT）触发各归各档。"""
    text_fn = lambda b: b.get("text") or ""
    assert not _is_history_geo_book(YUWEN_V1_BOOK, text_fn)
    assert _is_yuwen_book(YUWEN_V1_BOOK, text_fn)
    assert _select_flat_tier(YUWEN_V1_BOOK, text_fn) == "yuwen"
    assert not _is_history_geo_book(YINGYU_V1_BOOK, text_fn)
    assert not _is_yuwen_book(YINGYU_V1_BOOK, text_fn)
    assert _is_yingyu_book(YINGYU_V1_BOOK, text_fn)
    assert _select_flat_tier(YINGYU_V1_BOOK, text_fn) == "yingyu"


# 历史/地理共用一个政治书形态（汉字课号压平书），供互斥测试复用。
POLITICS_LIKE_BOOK = [
    _text(1, "思想 政治"),
    _text(2, "目录"),
    _text(None, "第一单元 中国共产党的领导 1\n第一课 历史和人民的选择 2\n"
                "中华人民共和国成立前各种政治力量 2", page=0),
    _text(2, "第一单元 中国共产党的领导", page=1),
    _text(2, "第一课历史和人民的选择", page=1),
    _text(2, "中华人民共和国成立前各种政治力量", page=1),
    _text(2, "第二课 中国共产党的先进性", page=5),
    _text(2, "始终坚持以人民为中心", page=5),
    _text(2, "第三课 坚持和加强党的全面领导", page=9),
    _text(2, "坚持党的领导", page=9),
    _text(2, "探究与分享", page=10),
]


# ── 历史/地理档 ────────────────────────────────────────────────────────

def test_history_geo_tier_levels_unit_lesson_section_mu():
    """历史：单元→2，课→3，目录/形态过的目→4；地理：章→2，节→3，目→4。"""
    tree, paths = build_tree(HISTORY_V1_BOOK, doc_id="h1")
    assert tree["title"] == "中外历史纲要(上)"
    units = tree["children"]
    assert len(units) == 1 and units[0]["level"] == 2
    lessons = units[0]["children"]
    assert [l["title"] for l in lessons] == [
        "第1课 中华文明的起源与早期国家",
        "第2课诸侯纷争与变法运动",
        "第 3 课 秦统一多民族封建国家的建立",
    ]
    assert all(l["level"] == 3 and "type" not in l for l in lessons)
    mus = mus_of(tree)
    # 每课 2 目上限：第1课收前两个目，"商和西周"（课内黑体小标题）被截断；
    # 第2课只收"列国纷争与华夏认同"。
    assert [m["title"] for m in mus] == [
        "石器时代的古人类和文化遗存",
        "从部落到国家",
        "列国纷争与华夏认同",
    ]
    assert all(m["level"] == 4 for m in mus)
    for m in mus:
        assert m["node_id"] == stable_node_id("h1", m["struct_path"])
    assert paths[10].endswith("第1课 中华文明的起源与早期国家/石器时代的古人类和文化遗存")
    # 超额目候选退回正文块，随当前课路径走。
    assert "商和西周" not in "".join(p for p in paths if p)

    geo_tree, _ = build_tree(GEO_V1_BOOK, doc_id="g1")
    nodes = _flatten(geo_tree["children"])
    chapters = [n for n in nodes if n["level"] == 2 and "type" not in n]
    assert [c["title"] for c in chapters] == [
        "第一章 宇宙中的地球",
        "第二章 地球上的大气",
    ]
    sections = [n for n in nodes if n["level"] == 3 and "type" not in n]
    assert "第一节 地球的宇宙环境" in [s["title"] for s in sections]
    assert [m["title"] for m in mus_of(geo_tree)] == [
        "地球在宇宙中的位置",
        "太阳辐射对地球的影响",
        "化石和地质年代表",
        "大气的组成",
    ]


def test_history_geo_mu_filters_columns_figures_and_activity_items():
    """目候选过滤：栏目黑名单、图注、编号条目、括号子目、图版资料全不收。"""
    for book in (HISTORY_V1_BOOK, GEO_V1_BOOK):
        tree, paths = build_tree(book, doc_id="hg")
        titles = {m["title"] for m in mus_of(tree)}
        for noise in ("学习聚焦", "史料阅读", "活动", "案例"):
            assert noise not in titles
        body_paths = "".join(p for p in paths if p)
        assert "学习聚焦" not in body_paths and "史料阅读" not in body_paths
    assert "▲ 战国形势图" not in {m["title"] for m in mus_of(build_tree(HISTORY_V1_BOOK, doc_id="h")[0])}
    geo_titles = {m["title"] for m in mus_of(build_tree(GEO_V1_BOOK, doc_id="g")[0])}
    for noise in ("开展简单的天文现象观测活动", "图 1.8 月相观测描绘示例", "(一) 行星地球", "资料 2 大气污染及其危害"):
        assert noise not in geo_titles


def test_history_footer_gate_and_prefixless_lesson_recovery():
    """页脚法门卫 + 丢前缀课标题的后缀回认（纲要上第5课 lvl1 裸题名实锤）。"""
    # 页脚没登记的课标题不开课（目录残留行/伪标题在此被拒）。
    gated = [
        _text(1, "历史", page=0),
        _text(2, "目录", page=0),
        _text(
            None,
            "第一单元 从中华文明起源到秦汉统一多民族封建国家\n"
            "第1课 中华文明的起源与早期国家 2\n第2课 诸侯纷争与变法运动 8\n"
            "第3课 秦统一多民族封建国家的建立 14",
            page=0,
        ),
        _text(2, "第一单元 从中华文明起源到秦汉统一多民族封建国家", page=1),
        _text(2, "第1课 中华文明的起源与早期国家", page=2),
        _text(2, "石器时代的古人类和文化遗存", page=2),
        _text(None, "1965年5月，中国地质科学院的研究人员……", page=2),
        _text(2, "第2课 诸侯纷争与变法运动", page=3),
        _text(None, "东周分为春秋、战国两个阶段……", page=3),
        _text(2, "第9课 王安石变法", page=3),  # 页脚未登记（目录残留）→ 拒
        _text(2, "第3课 秦统一多民族封建国家的建立", page=4),
        _footer("第1课 中华文明的起源与早期国家", page=2),
        _footer("第2课 诸侯纷争与变法运动", page=3),
        _footer("第3课 秦统一多民族封建国家的建立", page=4),
    ]
    tree, paths = build_tree(gated, doc_id="h2")
    lessons = [n for n in _flatten(tree["children"]) if n["level"] == 3]
    assert [l["title"] for l in lessons] == [
        "第1课 中华文明的起源与早期国家",
        "第2课 诸侯纷争与变法运动",
        "第3课 秦统一多民族封建国家的建立",
    ]
    assert "第9课 王安石变法" not in "".join(p for p in paths if p)
    # lvl1 裸题名 = 已登记课名去掉课号后的后缀 → 回认为课。
    recovered = [
        _text(1, "历史", page=0),
        _text(2, "目录", page=0),
        _text(
            None,
            "第4课 西汉与东汉——统一多民族封建国家的巩固 12\n"
            "第5课 三国两晋南北朝的政权更迭与民族交融 20\n"
            "第6课 从隋唐盛世到五代十国 26\n第7课 隋唐制度的变化与创新 30",
            page=0,
        ),
        _text(2, "第4课 西汉与东汉——统一多民族封建国家的巩固", page=12),
        _text(2, "东汉的兴衰", page=12),
        _text(None, "东汉外戚宦官交替专权……", page=12),
        _text(1, "三国两晋南北朝的政权更迭与民族交融", page=20),  # 丢前缀 lvl1
        _text(2, "三国与西晋", page=20),
        _text(None, "220年，曹丕称帝……", page=20),
        _text(2, "第6课 从隋唐盛世到五代十国", page=26),
        _text(2, "第7课 隋唐制度的变化与创新", page=30),
        _footer("第4课 西汉与东汉——统一多民族封建国家的巩固", page=12),
        _footer("第5课 三国两晋南北朝的政权更迭与民族交融", page=20),
        _footer("第6课 从隋唐盛世到五代十国", page=26),
        _footer("第7课 隋唐制度的变化与创新", page=30),
    ]
    tree2, _ = build_tree(recovered, doc_id="h3")
    lessons2 = [n for n in _flatten(tree2["children"]) if n["level"] == 3]
    assert [l["title"] for l in lessons2] == [
        "第4课 西汉与东汉——统一多民族封建国家的巩固",
        "三国两晋南北朝的政权更迭与民族交融",
        "第6课 从隋唐盛世到五代十国",
        "第7课 隋唐制度的变化与创新",
    ]
    assert [m["title"] for m in mus_of(tree2)] == ["东汉的兴衰", "三国与西晋"]


# ── 语文档 ─────────────────────────────────────────────────────────────

def test_yuwen_tier_levels_unit_and_toc_registered_lessons():
    """语文：单元→2，目录反查命中的课文标题（lvl 任意）→4 mu。"""
    tree, paths = build_tree(YUWEN_V1_BOOK, doc_id="y1")
    units = [n for n in tree["children"] if "type" not in n]
    assert [u["title"] for u in units] == ["第一单元", "第二单元"]
    assert all(u["level"] == 2 for u in units)
    mus = mus_of(tree)
    assert [m["title"] for m in mus] == [
        "沁园春·长沙",
        "立在地球边上放号",
        "红烛",
        "百合花",
    ]
    assert all(m["level"] == 4 for m in mus)
    for m in mus:
        assert m["node_id"] == stable_node_id("y1", m["struct_path"])
    # 正文块继承课文路径。
    assert paths[9].endswith("第一单元/沁园春·长沙")


def test_yuwen_mu_rejects_unregistered_and_blacklist_blocks():
    """非目录篇名（诗句行"红烛啊"、作者行）与栏目黑名单块不收。"""
    tree, paths = build_tree(YUWEN_V1_BOOK, doc_id="y2")
    titles = {m["title"] for m in mus_of(tree)}
    for noise in ("红烛啊", "毛泽东", "郭沫若", "学习提示", "单元学习任务", "研习任务", "目录"):
        assert noise not in titles
    body_paths = "".join(p for p in paths if p)
    assert "学习提示" not in body_paths and "单元学习任务" not in body_paths


# ── 英语档 ─────────────────────────────────────────────────────────────

def test_yingyu_tier_levels_units_and_section_rows():
    """英语：UNIT 标题→2，板块行（裸名/带前缀）→4 mu，lvl1 顶格行→2。"""
    tree, paths = build_tree(YINGYU_V1_BOOK, doc_id="e1")
    nodes = _flatten(tree["children"])
    units = [n for n in nodes if n["level"] == 2 and "type" not in n]
    assert [u["title"] for u in units] == [
        "The mass media",
        "UNIT 2 Sports culture",
        "UNIT 3 Fit for life",
    ]
    mus = mus_of(tree)
    assert [m["title"] for m in mus] == [
        "Welcome to the unit",
        "Reading",
        "Grammar and usage",
        "Project",
        "Unit 2 Sports culture Reading",
    ]
    assert all(m["level"] == 4 for m in mus)
    for m in mus:
        assert m["node_id"] == stable_node_id("e1", m["struct_path"])
    # 附录带前缀板块行挂在最近的单元之下。
    prefixed = next(p for p in paths if p.endswith("Unit 2 Sports culture Reading"))
    assert prefixed.startswith("UNIT 3 Fit for life/")


def test_yingyu_mu_rejects_answers_and_noise_rows():
    """答案/听力原文/Answer key 与师用书噪声（Possible answer/Notes/复现行）不收。"""
    tree, paths = build_tree(YINGYU_V1_BOOK, doc_id="e2")
    titles = {m["title"] for m in mus_of(tree)}
    for noise in ("Possible answer", "Notes", "内容分析", "一、单元概述",
                  "Unit 2 Sports culture", "Appendix II Answer key to Workbook", "答案"):
        assert noise not in titles
    body_paths = "".join(p for p in paths if p)
    assert "Answer key" not in body_paths and "Possible answer" not in body_paths
