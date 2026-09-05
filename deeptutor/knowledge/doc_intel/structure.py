"""Textbook structure tree extraction: 单元 → 课/章 → 节 → 目.

Signals, in priority order:
1. ``text_level`` on title/text blocks (MinerU v1 content_list) — level 1 is
   usually the unit/chapter, level 2 the lesson/section.
2. v2 ``title`` blocks with ``content.level``.
3. Heading regex fallback (第X单元 / 第X课 / X.X 节) when levels are absent
   (vlm products emit plain text blocks with levels only sometimes).
4. 目级 KP headings (level 4, ``type: "mu"``), the finest granularity K12
   textbooks carry:
   - MinerU ``text_level >= 3`` heading blocks (统编政史地/语文: 单元→课→目,
     MinerU tags the 目 with level 3+);
   - three-part numbered sub-sections (``6.2.1 排列`` under 节 ``6.2`` —
     MinerU flattens them to level 2, so numbering depth, not level, is the
     tell; verified on 人教A数学选必三 real products);
   - un-leveled text blocks carrying bold markers (``**目名**``) that strip
     to a ≤20-char 目-shaped noun phrase — the 黑体目级小标题 path.
5. 政治档 (P5): when a book's heading levels are flattened to
   ``text_level <= 2`` (封面 lvl1, 单元/课/目 all lvl2 — 必修3 实锤) AND its
   lvl2 blocks carry ≥3 ``第N课`` shapes, level comes from shape + 目录反查
   instead of the MinerU level: ``第N单元`` → 2, ``第N课`` → 3, other lvl2
   short blocks through the 栏目黑名单 + TOC reverse lookup → 目 (4).
6. 多档判级 (P6): the P5 tier generalizes into mutually-exclusive tiers
   picked per book (:func:`_select_flat_tier`, 数学原生档 → 政治档 →
   历史/地理档 → 语文档 → 英语档):
   - 历史/地理档 — P5's shape rules extended with Arabic lesson numbers
     (``第1课``) and 章/节 (``第N章``/``第N节``); 目 = lvl2 short noun
     phrases through the 栏目黑名单 (学习聚焦/史料阅读/活动/案例…), TOC
     reverse lookup applied only when the TOC actually lists 目名.
   - 语文档 — 课文标题 lvl 混乱 (None/1/2 混杂), so 目判定 leans on the
     TOC: 目录课文行 "1 沁园春·长沙/毛泽东 2" registers 课文名, and any
     body title block matching the register becomes a mu.
   - 英语档 — UNIT titles at lvl1 (``UNIT 2 Sports culture``), 目 = lvl2
     板块行 (Reading/Exploring language/…, bare or "Unit 2 … Reading").

Output: per-block ``struct_path`` like ``必修一/第1章 集合/1.1 集合的概念``,
plus the doc-level tree for the T021 textbook-tree API. Every tree node also
carries a stable ``node_id`` (``sha1(doc_id|struct_path)[:12]``) so knowledge
points and questions derived from a node can bridge back to it by id instead
of fuzzy title matching.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
from typing import Any

from deeptutor.textbook_struct.column_blacklist import COLUMN_BLACKLIST

# Heading patterns for Chinese K12 textbooks / workbooks.
_UNIT_RE = re.compile(r"^第\s*[一二三四五六七八九十\d]+\s*(单元|部分|章)([　\s.、:：]*(.*))?$")
_LESSON_RE = re.compile(r"^第\s*[一二三四五六七八九十\d]+\s*课([　\s.、:：-]*(.*))?$")
_FRAME_RE = re.compile(r"^第\s*[一二三四五六七八九十\d]+\s*(?:节\s*)?框([　\s.、:：-]*(.*))?$")
_SECTION_RE = re.compile(r"^(\d+[.．]\d*[　\s]*.{0,30})$|^[一二三四五六七八九十]+[、.．]\s*(.{1,20})$")
_CHAPTER_NUM_RE = re.compile(r"^第\s*(\d+)\s*章")
_PART_RE = re.compile(r"^(必修|选择性必修|选修)\s*([一二三123])")

# Structural-section noise that MinerU tags as headings inside textbooks:
# 练习/习题/感受·理解 level bars and bare numeric section labels. Real
# sections carry a name; practice headers are page furniture.
_PRACTICE_RE = re.compile(r"^(练习|习题[\d.]*|感受[·.]理解|思考[·.]运用|探究[·.]拓展|思考|阅读|链接|实习|作业|复习参考题|本章小结|单元?提升|综合探究)")
_BARE_NUM_RE = re.compile(r"^\d+[.．]\d*$")
# A real section heading: numbered label possibly on its own line, name on next.
_TITLED_SECTION_RE = re.compile(r"^\d+[.．]\d+")
# Book furniture: cover / TOC-page headings that precede any real structure.
_BOOK_FURNITURE_RE = re.compile(
    r"^(普通高中教科书|义务教育教科书|高中数学|数学\s*[A-Za-z ]*|语文|英语|物理|化学|生物|政治|历史|地理|"
    r"SHU\s*XUE|CHINESE|目录|目\s*录|致同学|前言|编写说明|本书符号|部分参考答案)$"
)

# ── 目级 (level 4) signals ──────────────────────────────────────────────
#: A mu candidate is a noun phrase: CJK-bearing, no sentence punctuation,
#: short (K12 目 names never run past ~20 chars once bold marks are gone).
_MU_MAX_LEN = 20
#: Three-part sub-section numbering ("6.2.1 排列") — a 目 under 节 6.2.
_SUB_SUB_NUM_RE = re.compile(r"^\d{1,2}[.．]\d{1,2}[.．]\d{1,2}[　\s.、:：]*(.*)$")
#: Inline bold markers as MinerU v1 emits them (markdown-flavoured text).
_BOLD_MARK_RE = re.compile(r"\*\*")
#: Sentence punctuation: a body sentence, never a 目 heading.
_MU_SENTENCE_RE = re.compile(r"[。？！?!；;，,：:]")


# ── 政治档（低层级书适配, P5）───────────────────────────────────────────
# 政治/道法类教材 MinerU 常把全书标题压平在 text_level 2（封面 lvl1，单元/
# 课/目全部 lvl2），B2-a 的三层判据（level≥3 / X.Y.Z / 加粗短块）全不命中。
# 判级改靠形态 + 目录反查：第N单元 → level 2，第N课 → level 3，其余 lvl2
# 短块过栏目黑名单 + 目录页反查 → 目 (4)。
_POLITICS_UNIT_RE = re.compile(r"^第\s*[一二三四五六七八九十百\d]+\s*单元")
_POLITICS_LESSON_RE = re.compile(r"^第\s*[一二三四五六七八九十百\d]+\s*课")
#: 触发门槛：lvl2 中 第N课 标题 ≥3 且全书 max(text_level)≤2（政治书实锤，
#: 必修3 的 9 课中 5 课被压在 lvl2，正文小标题同为 lvl2）。
_POLITICS_MIN_LESSONS = 3
#: 政治档触发只认**汉字数字**课号（第一课…）。阿拉伯数字课号（第1课，中外
#: 历史纲要实锤）留给历史/地理档——两档触发互斥，避免历史书误入政治档后
#: 目录反查落空（历史目录只列单元/课，不列目名）。判级正则仍保留 \d，兼容
#: 个别用阿拉伯数字编课号的政治书（落档后判级照常）。
_POLITICS_TRIGGER_LESSON_RE = re.compile(r"^第\s*[一二三四五六七八九十百]+\s*课")
#: 目录反查窗：书前部前 N 块内的目录块。
_POLITICS_TOC_WINDOW = 60
#: 目名登记的最短长度（"目 录"这类两字行不登记）。
_POLITICS_MU_MIN_LEN = 4
#: 政治固定栏目黑名单（COLUMN_BLACKLIST 的政治档扩展，封闭词表可续加）。
POLITICS_COLUMN_BLACKLIST: frozenset[str] = frozenset(
    {"学思之窗", "名言", "演示", "决策", "历史回声"}
)
#: 目录行形态（去空白后）：CJK/标点串 + 尾随页码数字。
_POLITICS_TOC_ROW_RE = re.compile(r"([一-鿿][一-鿿：:、，,。；;·（）()\-—]*?)\d+")
#: 整块目录形态：≥3 个"目名+页码"行连续相接（出版页/封面数字串不满足）。
_POLITICS_TOC_BLOCK_RE = re.compile(
    r"(?:[一-鿿][一-鿿：:、，,。；;·（）()\-—]{3,}\d+){3,}"
)
#: 首尾装饰符号（◆◆◆ 名词点击 / 严格执法 |）。
_POLITICS_DECOR_RE = re.compile(r"^[^一-鿿]+|[^一-鿿]+$")
#: 目名里的间隔标点——截断处的前缀也登记（"人民代表大会制度：我国的根本
#: 政治制度"的正文子标题"人民代表大会制度的优势"靠前缀命中）。
_POLITICS_NAME_SPLIT_RE = re.compile(r"[：:、，,。；;·]")


def _politics_deco(text: str) -> str:
    """Strip decorative leading/trailing non-CJK symbols from a heading."""
    return _POLITICS_DECOR_RE.sub("", (text or "").strip())


def _is_politics_book(blocks: list[dict], text_fn) -> bool:
    """政治档触发：max(text_level)≤2 且 lvl2 中 汉字数字 第N课 标题 ≥3。

    自动检测、不认书名：数学/物理书的标题层级到 3 以上，或 课 标题不以
    lvl2 出现，都不触发。阿拉伯数字课号（第1课）不算——那是历史书的形态
    （见 :data:`_POLITICS_TRIGGER_LESSON_RE` 注）。
    """
    lessons = 0
    max_level = 0
    for block in blocks:
        lvl = block.get("text_level")
        if isinstance(lvl, int) and lvl > 0:
            if lvl > max_level:
                max_level = lvl
            if lvl == 2 and _POLITICS_TRIGGER_LESSON_RE.match(_squash(text_fn(block))):
                lessons += 1
    return max_level <= 2 and lessons >= _POLITICS_MIN_LESSONS


def _politics_toc_register(blocks: list[dict], text_fn) -> frozenset[str]:
    """目录页反查登记表：书前部目录行里的目名（去空白）。

    目录是教材权威结构——正文 lvl2 短标题只有在目录中出现（或以目录名去
    标点前缀开头）才收为 mu，出版页（人民教育出版社）与正文小标题被反查
    排除。目录块整块须呈"目名+页码"行连续形态（≥3 行），单行噪声（封面
    年份、出版社地址）不登记；单元/课/综合探究行是结构行，不入目名表。
    """
    names: set[str] = set()
    for block in blocks[:_POLITICS_TOC_WINDOW]:
        squashed = _squash(text_fn(block))
        if not _POLITICS_TOC_BLOCK_RE.fullmatch(squashed):
            continue
        for name in _POLITICS_TOC_ROW_RE.findall(squashed):
            if _POLITICS_UNIT_RE.match(name) or _POLITICS_LESSON_RE.match(name):
                continue
            if name.startswith("综合探究"):
                continue
            if len(name) >= _POLITICS_MU_MIN_LEN:
                names.add(name)
            prefix = _POLITICS_NAME_SPLIT_RE.split(name, 1)[0]
            if len(prefix) >= _POLITICS_MU_MIN_LEN:
                names.add(prefix)
    return frozenset(names)


def _politics_mu_ok(text: str, register: frozenset[str]) -> bool:
    """目候选过滤：栏目黑名单 + 目录反查（两道都过才算 mu）。"""
    clean = _politics_deco(text)
    if not _has_cjk(clean) or len(clean) > _MU_MAX_LEN:
        return False
    if clean in COLUMN_BLACKLIST or clean in POLITICS_COLUMN_BLACKLIST:
        return False
    squashed = _squash(clean)
    if not squashed:
        return False
    return any(squashed.startswith(name) or name.startswith(squashed) for name in register)


def _politics_level(block: dict, text: str, register: frozenset[str]) -> int | None:
    """政治档判级：第N单元→2，第N课→3，lvl2 目候选→4，其余 None。

    单元/课按形态判级（不分 MinerU 层级——政治书把它们 lvl1/lvl2 混排）；
    其余 lvl1（封面书名/出版页）与未分级块忽略；lvl2 短块过两道过滤后为
    目 (level 4)。
    """
    if not text:
        return None
    squashed = _squash(text)
    if _POLITICS_UNIT_RE.match(squashed):
        return 2
    if _POLITICS_LESSON_RE.match(squashed):
        return 3
    if block.get("text_level") != 2:
        return None  # 封面/出版页 (lvl1) 与正文块：不开结构
    if _politics_mu_ok(text, register):
        return 4
    return None


# ── 多档判级（P6）：低层级压平书的分档框架 ──────────────────────────────
# P5 政治档实弹后，解剖确认历史/地理/语文/英语四科书同为"低层级压平"形态
# （封面 lvl1、全书结构标题 ≤lvl2，B2-a 三层判据全不命中）。在 P5 框架上
# 泛化为多档，每档 = 触发条件 + 结构正则 + 目过滤，按书自动选择，触发互斥
# 按序匹配：数学原生档 → 政治档 → 历史/地理档 → 语文档 → 英语档。
#
#: 历史/地理档结构正则：单元/章 → level 2，课/节 → level 3（P5 正则的
#: 阿拉伯数字扩展——历史"第1课"、地理"第一节/第N章"，中阿混排共存）。
_HISTORY_GEO_UNIT_RE = re.compile(r"^第\s*[一二三四五六七八九十百\d]+\s*(?:单元|章)")
_HISTORY_GEO_LESSON_RE = re.compile(r"^第\s*[一二三四五六七八九十百\d]+\s*(?:课|节)")
#: 历史/地理档触发形态：阿拉伯数字课号（历史，中外历史纲要实锤"第1课"）
#: 与 节 号（地理，必修一"第一节"每章重复）。其余同政治档：max_level≤2。
_HISTORY_GEO_ARABIC_LESSON_RE = re.compile(r"^第\s*\d+\s*课")
_HISTORY_GEO_SECTION_RE = re.compile(r"^第\s*[一二三四五六七八九十\d]+\s*节")
#: 触发门槛（同政治档 3 课的量级：一本课制书不会少于 3 个结构标题）。
_HISTORY_GEO_MIN_STRUCTURE = 3
#: 历史/地理固定栏目黑名单（COLUMN_BLACKLIST + 政治档扩展的再扩展；封闭
#: 词表，实测校准可续加——样例：中外历史纲要上、人教地理必修一）。
HISTORY_GEO_COLUMN_BLACKLIST: frozenset[str] = frozenset(
    {
        "学习聚焦", "史料阅读", "历史纵横", "思考点", "问题探究", "学习拓展",
        "探究与拓展", "学思之窗", "名词点击", "名言", "演示", "决策", "历史回声",
        # 地理（必修一实锤）
        "问题探讨", "知识拓展", "资料分析", "本章要点", "背景知识",
        # 活动课 / 书尾（历史纲要上"活动课 家国情怀…"）
        "活动课", "活动主题", "活动目标", "活动过程", "活动拓展",
        "分工设计", "对策和建议",
        "步骤一", "步骤二", "步骤三", "步骤四", "步骤五", "后记", "附录",
    }
)
#: 目候选的形态噪声：图注（▲ 战国形势图 / 图 1.8 …）、编号活动条目
#: （"1. 确定月亮位置的方法"）、括号子目（"(一) 前寒武纪"）、图版资料
#: （"资料 2 …"）、书尾附录行（"附录二 本套书常用地图图例"）。
_HISTORY_GEO_FIGURE_RE = re.compile(r"^[▲◀▶▼]|^图\s*\d")
_HISTORY_GEO_NUM_ITEM_RE = re.compile(r"^\d{1,2}[.．、]\s*\S")
_HISTORY_GEO_PAREN_SUB_RE = re.compile(r"^[（(]\s*[一二三四五六七八九十\d]{1,3}\s*[)）]")
_HISTORY_GEO_MATERIAL_RE = re.compile(r"^资料\s*\d")
_HISTORY_GEO_PREFIX_NOISE_RE = re.compile(r"^(附录|活动课|步骤[一二三四五])")
#: 活动/问题研究的**动词开头**条目（"分析太阳黑子的变化周期"）：目名是
#: 名词短语，动词开头的是活动指令（地理必修一实锤）。
_HISTORY_GEO_VERB_HEAD_RE = re.compile(
    r"^(分析|认识|了解|观察|通过|根据|绘制|利用|形成|说明|开展|讨论|设计|收集|整理|对比|查阅|结合|探讨|总结|思考)"
)
#: 截断/表格残行的收尾虚词（"毛泽东关于"）：真目名不以虚词收尾。
_HISTORY_GEO_TRAILING_PARTICLE_RE = re.compile(r"(的|与|和|或|及|在|对|从|向|关于|了|着)$")
#: 目候选的 squash 长度上限（历史/地理目名更短：真目名 ≤16 字，超长的
#: 是活动指令或栏目内容标题——"分析海水温度对游泳活动的影响"）。
_HISTORY_GEO_MU_MAX_SQUASH = 16
#: 每个父节点（课/节）收的目数上限。压平档无法从形态区分一级目与课内黑体
#: 小标题（纲要上第1课"商和西周"与目"从部落到国家"同为 lvl2 短块），超出
#: 部分按文档序截断——一课 2-3 个目是教参 KP 粒度的常态（P5 政治档同语义）。
_HISTORY_GEO_MU_PER_PARENT = 2

# ── 语文档（P6）────────────────────────────────────────────────────────
# 统编语文课文标题 lvl 混乱（None/1/2 混杂："沁园春·长沙"None、"百合花"
# lvl1），单元/课文两级结构，lvl2 稀疏。目判定以**目录页反查为主**：目录
# 课文行"1 沁园春·长沙/毛泽东 2"，正文标题块（lvl 任意）与目录目名精确/
# 高相似匹配即 mu；正文 lvl 一律不作数。
#: 目录课文行（squash 形态，跨换行/空格差异——build_tree 默认 text_fn 会把
#: 换行折叠成空格，行锚定不可靠）：可选自读标记 \* + 课程号 + 篇名/作者 +
#: 尾页码。作者后的页码数字是行终止符。
_YUWEN_COURSE_ROW_RE = re.compile(
    r"[\\*＊]?\d{0,2}([一-鿿][^/＊\\]{1,24})/([^/＊\\]{1,16}?)\d{1,3}"
)
#: 行首残留（squash 后"第一单元1沁园春·长沙"——单元行与课文行挤在一行）。
_YUWEN_UNIT_ROW_RE = re.compile(r"^第\s*[一二三四五六七八九十\d]+\s*单元")
_YUWEN_ROW_NUM_RE = re.compile(r"^\d{1,2}")
#: 触发门槛：目录页含 单元/课文 结构（≥2 个单元行 + ≥4 条课文行）且 lvl2
#: 稀疏（<10% 块——语文 92/2058=4.5%，历史/地理/英语 14-23%）。
_YUWEN_MAX_LVL2_RATIO = 0.10
_YUWEN_MIN_UNITS = 2
_YUWEN_MIN_COURSES = 4
#: 语文固定栏目黑名单（COLUMN_BLACKLIST 已含 学习提示/单元导语）。
YUWEN_COLUMN_BLACKLIST: frozenset[str] = frozenset(
    {"单元学习任务", "研习任务", "单元学习任务（一）", "研习任务（一）"}
)
#: 课文标题带的注释角标（"沁园春·长沙①"、"红烛③"）——匹配前剥掉。
_YUWEN_ANNOT_RE = re.compile(r"[①-⑳]+[\\*]?$")
#: 行内 LaTeX 上标残留（"红烛 $^{③}$"，MinerU 数学式输出习惯）。
_YUWEN_SUP_RE = re.compile(r"\s*\$\^\{?[^}]*\}?\$")

# ── 英语档（P6）────────────────────────────────────────────────────────
# 教师教学用书（译林）：UNIT 标题 lvl1（"UNIT 2 Sports culture"），板块行
# lvl2（正文裸板块名 "Reading"，附录带前缀 "Unit 2 Sports culture Reading"）。
#: 触发：lvl1 的 UNIT N 标题 ≥2（"UNIT 2"/"UNIT 3" 实锤）。
_YINGYU_UNIT_LV1_RE = re.compile(r"^UNIT\s*\d+", re.IGNORECASE)
_YINGYU_MIN_UNITS = 2
#: 结构正则：UNIT N（不限大小写；squash 后无词边界，\b 会失配
#: "unit2sportsculture"——登记与判级都在去空白形态上跑）。
_YINGYU_UNIT_RE = re.compile(r"^UNIT\s*(\d+)", re.IGNORECASE)
#: lvl1 非 UNIT 行（"The mass media"——第一单元导读区标题）→ level 2。
_YINGYU_BOOK_FURNITURE_RE = re.compile(r"^《|^[一-鿿]{1,4}$")
#: 英语板块名封闭白名单（mu 判定的反向用法：板块行是封闭集合，比黑名单
#: 稳——"Possible answer"/"Notes"/"A1" 等师用书噪声与真板块同为 Title-Case）。
YINGYU_SECTION_WHITELIST: frozenset[str] = frozenset(
    {
        "Welcome to the unit", "Reading", "Grammar and usage", "Integrated skills",
        "Extended reading", "Project", "Assessment", "Further study",
        "Exploring language", "Video script",
    }
)
#: 师用书噪声黑名单（答案/听力原文/附录行）。
YINGYU_COLUMN_BLACKLIST: frozenset[str] = frozenset(
    {"答案", "听力原文", "Answer keys", "Appendix", "Answer", "Notes", "Possible answer"}
)
_YINGYU_ANSWER_RE = re.compile(r"answer\s*key|听力原文", re.IGNORECASE)


def _select_flat_tier(blocks: list[dict], text_fn) -> str:
    """多档触发互斥、按序匹配：政治 → 历史/地理 → 语文 → 英语。

    数学等原生分层书（max_level≥3 或章制 X.Y 节制）一档都不命中，走
    B2-a 原生判据。单书命中即用。
    """
    if _is_politics_book(blocks, text_fn):
        return "politics"
    if _is_history_geo_book(blocks, text_fn):
        return "history_geo"
    if _is_yuwen_book(blocks, text_fn):
        return "yuwen"
    if _is_yingyu_book(blocks, text_fn):
        return "yingyu"
    return ""


def _is_history_geo_book(blocks: list[dict], text_fn) -> bool:
    """历史/地理档触发：max(text_level)≤2 且 阿拉伯课号课 ≥3 或 节 ≥3。

    汉字数字课号已被政治档先行认领（触发互斥），此档只接阿拉伯课号
    （历史）与 节制书（地理）。
    """
    lessons = 0
    sections = 0
    max_level = 0
    for block in blocks:
        lvl = block.get("text_level")
        if not isinstance(lvl, int) or lvl <= 0:
            continue
        if lvl > max_level:
            max_level = lvl
        squashed = _squash(text_fn(block))
        if _HISTORY_GEO_ARABIC_LESSON_RE.match(squashed):
            lessons += 1
        elif _HISTORY_GEO_SECTION_RE.match(squashed):
            sections += 1
    return max_level <= 2 and (lessons >= _HISTORY_GEO_MIN_STRUCTURE or sections >= _HISTORY_GEO_MIN_STRUCTURE)


def _is_yuwen_book(blocks: list[dict], text_fn) -> bool:
    """语文档触发：lvl2 稀疏（<10% 块）+ 目录页含 单元/课文 行组。"""
    if not blocks:
        return False
    lvl2 = sum(1 for b in blocks if b.get("text_level") == 2)
    if lvl2 / len(blocks) >= _YUWEN_MAX_LVL2_RATIO:
        return False
    units = 0
    courses = 0
    for block in blocks[:_POLITICS_TOC_WINDOW]:
        text = _squash(text_fn(block))
        units += len(re.findall(r"第\s*[一二三四五六七八九十\d]+\s*单元", text))
        courses += len(_YUWEN_COURSE_ROW_RE.findall(text))
    return units >= _YUWEN_MIN_UNITS and courses >= _YUWEN_MIN_COURSES


def _is_yingyu_book(blocks: list[dict], text_fn) -> bool:
    """英语档触发：lvl1 的 UNIT N 标题 ≥2。"""
    units = 0
    for block in blocks:
        if block.get("text_level") == 1 and _YINGYU_UNIT_LV1_RE.match(_squash(text_fn(block))):
            units += 1
    return units >= _YINGYU_MIN_UNITS


def _is_history_geo_column(text: str) -> bool:
    """历史/地理栏目名（通用黑名单 + 本档扩展）的整块判定。"""
    clean = _politics_deco(text)
    return clean in COLUMN_BLACKLIST or clean in HISTORY_GEO_COLUMN_BLACKLIST


def _history_geo_level(
    block: dict,
    text: str,
    prev_column: bool,
    register: frozenset[str],
    lesson_suffixes: frozenset[str],
) -> int | None:
    """历史/地理档判级：单元/章→2，课/节→3，lvl2 目候选→4，其余 None。

    形态优先（不分 MinerU 层级——纲要上的课在 lvl1/lvl2 混排）；页脚登记
    过的课名去掉课号后的后缀可回认丢前缀的课标题（"第5课 三国两晋南北朝
    的政权更迭与民族交融"被压成 lvl1 裸题名实锤）。其余 lvl1（封面/活动
    课题名）与未分级块不开结构。
    """
    if not text:
        return None
    squashed = _squash(text)
    if _HISTORY_GEO_UNIT_RE.match(squashed):
        return 2
    if _HISTORY_GEO_LESSON_RE.match(squashed):
        return 3
    if block.get("text_level") == 1 and squashed in lesson_suffixes:
        return 3
    if block.get("text_level") != 2:
        return None
    # 栏目块的**紧邻**后块是栏目内容标题（"历史纵横"/"夏商时期的历法"），
    # 不是目——隔了正文就不算（真目名前常有栏目收尾块）。
    if prev_column or _is_history_geo_column(text):
        return None
    if _history_geo_mu_ok(text, register):
        return 4
    return None


def _yuwen_level(block: dict, text: str, register: frozenset[str]) -> int | None:
    """语文档判级：第N单元→2，目录反查命中的课文标题→4 (mu)，其余 None。

    正文标题 lvl 任意（None/1/2 混杂，必修上实锤）——判级只看形态 + 反查。
    """
    if not text:
        return None
    if re.match(r"^第\s*[一二三四五六七八九十\d]+\s*单元", _squash(text)):
        return 2
    if _yuwen_mu_ok(block, text, register):
        return 4
    return None


def _history_geo_mu_ok(text: str, register: frozenset[str]) -> bool:
    """历史/地理目候选过滤：形态 + 栏目黑名单（+ 目录反查，反查表非空时）。

    历史/地理目录只列 单元/课（章/节），不列目名——反查表为空是常态，此时
    目判定靠形态（短名词短语，见 _HISTORY_GEO_* 各正则）。若某书目录带目
    名（反查表非空），则照 P5 语义两道都过才算 mu。
    """
    clean = _politics_deco(text)
    if not _has_cjk(clean):
        return False
    squashed = _squash(clean)
    if not squashed or len(squashed) > _HISTORY_GEO_MU_MAX_SQUASH:
        return False
    if clean in COLUMN_BLACKLIST or clean in HISTORY_GEO_COLUMN_BLACKLIST:
        return False
    raw = text.strip()
    if (
        _HISTORY_GEO_FIGURE_RE.match(raw)
        or _HISTORY_GEO_NUM_ITEM_RE.match(raw)
        or _HISTORY_GEO_PAREN_SUB_RE.match(raw)
        or _HISTORY_GEO_MATERIAL_RE.match(raw)
        or _HISTORY_GEO_PREFIX_NOISE_RE.match(raw)
        or _HISTORY_GEO_VERB_HEAD_RE.match(clean)
        or _HISTORY_GEO_TRAILING_PARTICLE_RE.search(squashed)
    ):
        return False
    if register and not any(
        squashed.startswith(_squash(name)) or _squash(name).startswith(squashed)
        for name in register
    ):
        return False
    return True


def _history_geo_toc_register(blocks: list[dict], text_fn) -> frozenset[str]:
    """历史/地理目录反查表：排除结构行（单元/课/章/节/活动课/附录）后的
    目录行名。四科实锤目录都不列目名 → 空表 → 目判定走形态过滤。"""
    names: set[str] = set()
    for block in blocks[:_POLITICS_TOC_WINDOW]:
        squashed = _squash(text_fn(block))
        if not _POLITICS_TOC_BLOCK_RE.fullmatch(squashed):
            continue
        for name in _POLITICS_TOC_ROW_RE.findall(squashed):
            if (
                _HISTORY_GEO_UNIT_RE.match(name)
                or _HISTORY_GEO_LESSON_RE.match(name)
                or name.startswith(("活动课", "附录", "问题研究"))
            ):
                continue
            if len(name) >= _POLITICS_MU_MIN_LEN:
                names.add(name)
                prefix = _POLITICS_NAME_SPLIT_RE.split(name, 1)[0]
                if len(prefix) >= _POLITICS_MU_MIN_LEN:
                    names.add(prefix)
    return frozenset(names)


def _yuwen_toc_register(blocks: list[dict], text_fn) -> frozenset[str]:
    """语文目录反查表：目录课文行的篇名（"1 沁园春·长沙/毛泽东 2" →
    沁园春·长沙；自读行"\\* 红烛/闻一多 4" → 红烛）。行解析在去空白形态
    上跑（见 _YUWEN_COURSE_ROW_RE 注），单元行残留与课程号剥掉。篇名含
    间隔标点时前缀同登（P5 语义）。"""
    names: set[str] = set()
    for block in blocks[:_POLITICS_TOC_WINDOW]:
        for raw_name, _author in _YUWEN_COURSE_ROW_RE.findall(_squash(text_fn(block))):
            name = _YUWEN_UNIT_ROW_RE.sub("", raw_name)
            name = _YUWEN_ROW_NUM_RE.sub("", name)
            if len(name) >= 2:
                names.add(name)
                prefix = _POLITICS_NAME_SPLIT_RE.split(name, 1)[0]
                if len(prefix) >= 2:
                    names.add(prefix)
    return frozenset(names)


def _yuwen_mu_ok(block: dict, text: str, register: frozenset[str]) -> bool:
    """语文目（课文）判定：目录反查为主，正文 lvl 任意（None/1/2）。

    角标（"沁园春·长沙①"、"红烛 $^{③}$"）与装饰符剥掉后与目录篇名
    **精确**匹配（去空白比较——正文"静 女"对目录"静女"）。前缀互含会把
    课文内的诗句行（"红烛啊"）误收为课文，不用。
    """
    if block.get("type") not in (None, "text", "title", "paragraph"):
        return False
    clean = _politics_deco(_YUWEN_ANNOT_RE.sub("", _YUWEN_SUP_RE.sub("", text.strip())))
    if not _has_cjk(clean) or len(clean) > _MU_MAX_LEN:
        return False
    if clean in COLUMN_BLACKLIST or clean in YUWEN_COLUMN_BLACKLIST:
        return False
    if _MU_SENTENCE_RE.search(clean):
        return False
    return _squash(clean) in {_squash(name) for name in register}


def _yingyu_unit_register(blocks: list[dict], text_fn) -> frozenset[str]:
    """英语单元名登记表：lvl1 UNIT 标题行的单元名（"UNIT 2 Sports culture"
    → "unit2sportsculture"，小写归一），用于识别附录里"Unit 2 Sports
    culture Reading"这类带前缀的板块行。"""
    names: set[str] = set()
    for block in blocks:
        if block.get("text_level") != 1:
            continue
        squashed = _squash(text_fn(block)).lower()
        m = _YINGYU_UNIT_RE.match(squashed)
        if m and len(squashed) > m.end():
            names.add(squashed)
    return frozenset(names)


def _yingyu_level(block: dict, text: str, unit_register: frozenset[str]) -> int | None:
    """英语档判级：lvl2 板块行 → 4 (mu)；UNIT 标题/lvl1 顶格行 → 2；其余
    （师用书噪声 Possible answer/A1/教学目标…）→ None。

    板块行两种形态：正文裸板块名（"Reading"，白名单命中即 mu）；附录带
    前缀行（"Unit 2 Sports culture Reading"——squash 等于已登记单元名 +
    板块名）。板块判定先于 UNIT 结构判定（附录行同以 Unit N 开头，剥掉
    板块名后才是单元行）。附录答案区（Answer key / 答案）整块拉黑。
    大小写不敏感（UNIT 标题大写、附录行首字母大写，实锤两种形态并存）。
    """
    if not text:
        return None
    clean = text.strip()
    if _YINGYU_ANSWER_RE.search(clean) or clean in YINGYU_COLUMN_BLACKLIST:
        return None
    lvl = block.get("text_level")
    lowered = _squash(text).lower()
    if lvl == 2:
        for section in YINGYU_SECTION_WHITELIST:
            section_squashed = _squash(section).lower()
            if lowered == section_squashed:
                return 4
            for unit in unit_register:
                if (
                    lowered.startswith(unit)
                    and lowered.endswith(section_squashed)
                    and len(lowered) >= len(unit) + len(section_squashed)
                ):
                    return 4
        return None
    if lvl == 1 and not _YINGYU_BOOK_FURNITURE_RE.match(clean):
        # UNIT 标题与第一单元导读区顶格标题（"The mass media"）同开结构。
        return 2
    return None


def _strip_bold(text: str) -> tuple[str, bool]:
    """Strip markdown bold markers; return ``(clean_text, had_bold)``."""
    if "**" not in text:
        return text, False
    return _norm(text.replace("**", "")), True


def _mu_phrase_ok(text: str) -> bool:
    """True when *text* is a 目-shaped noun phrase (判据 2 phrase form)."""
    if not text or len(text) > _MU_MAX_LEN:
        return False
    if not _has_cjk(text):
        return False
    if _MU_SENTENCE_RE.search(text):
        return False
    if _norm(text) in COLUMN_BLACKLIST:
        return False
    if _PRACTICE_RE.match(text) or _BARE_NUM_RE.match(text) or _BOOK_FURNITURE_RE.match(text):
        return False
    # Higher-structure shapes belong to levels 1-3 — numbered 节 ("1.1 …"),
    # 课/框/章. Three-part numbering (6.2.1) is itself a 目, so it passes.
    if _SUB_SUB_NUM_RE.match(text) is None and _TITLED_SECTION_RE.match(text):
        return False
    if _PART_RE.match(text) or _UNIT_RE.match(text) or _LESSON_RE.match(text):
        return False
    if _FRAME_RE.match(text) or _CHAPTER_NUM_RE.match(text):
        return False
    return True


def _mu_level(block: dict, text: str) -> int | None:
    """4 when the block is a 目 heading (level-4 KP node), else None.

    Deterministic only — no LLM. Fires on (判据 1) explicit MinerU levels
    ≥3, (X.Y.Z) three-part sub-section numbering, and (判据 2) bold-marked
    short noun phrases on un-leveled blocks. 栏目黑名单 and furniture
    filters apply to every path.
    """
    if not text:
        return None
    raw = block.get("text_level")
    if not isinstance(raw, int) or raw <= 0:
        raw = None  # v1 only; v2 titles come through content.level upstream
    if raw is not None:
        if raw >= 3 and _content_level_mu_ok(text):
            return 4
        return None  # explicit level ≤2: mu can only come from numbering
    # No explicit level — the bold path (判据 2).
    stripped, had_bold = _strip_bold(text)
    if had_bold and _mu_phrase_ok(stripped):
        return 4
    return None


def _content_level_mu_ok(text: str) -> bool:
    """Furniture guard shared by the level≥3 mu path."""
    if _PRACTICE_RE.match(text) or _BARE_NUM_RE.match(text):
        return False
    if _norm(text) in COLUMN_BLACKLIST:
        return False
    # Numbered exercise stems / body sentences mis-tagged as headings: a
    # short heading may carry a colon ("目标导学：xx"), a long punctuated
    # line is body text.
    if _MU_SENTENCE_RE.search(text) and len(text) > 12:
        return False
    return True


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _squash(text: str) -> str:
    """Blank-free key for the footer register: 正文标题的空格习惯不稳定
    （"第七章 随机变量" vs "第七章随机变量"），登记与查询必须跨空格命中。"""
    return re.sub(r"\s+", "", (text or ""))


def _block_text_v1(block: dict) -> str:
    return _norm(block.get("text", ""))


def _block_text_v2(block: dict) -> str:
    content = block.get("content") or {}
    if block.get("type") == "title":
        parts = content.get("title_content") or []
        return _norm("".join(p.get("content", "") for p in parts if isinstance(p, dict)))
    if block.get("type") == "paragraph":
        parts = content.get("paragraph_content") or []
        return _norm("".join(p.get("content", "") for p in parts if isinstance(p, dict)))
    return ""


def _block_level(block: dict, text: str) -> int | None:
    """1-based heading level, or None for body blocks."""
    if block.get("type") == "title":  # v2
        lvl = ((block.get("content") or {}).get("level")) or 0
        if isinstance(lvl, int) and lvl > 0:
            return _content_level(text, lvl)
    lvl = block.get("text_level")  # v1
    if isinstance(lvl, int) and lvl > 0:
        return _content_level(text, lvl)
    # Regex fallback on the text itself. Only fires when MinerU gave no level,
    # so it must be conservative: require an explicit unit/lesson/section
    # keyword — bare "N. something" lines are exercise stems, not headings.
    if _PART_RE.match(text) or _UNIT_RE.match(text):
        return 1
    if _LESSON_RE.match(text) or _CHAPTER_NUM_RE.match(text):
        return 2
    if _FRAME_RE.match(text):
        return 3
    if (
        _TITLED_SECTION_RE.match(text)
        and len(text) <= 34
        and not re.search(r"[。？?！!,，：:]", text)
        and _has_cjk(text)
    ):
        return 3
    # Bold-marked 目 phrase (判据 2) — checked last so structural shapes above
    # keep their levels 1-3.
    return _mu_level(block, text)


def _has_cjk(text: str) -> bool:
    """True when the text carries at least one CJK character.

    A numbered section heading is ``编号 + 中文标题`` ("6.1 分类加法计数原理").
    Cover-page OCR noise is digits-and-spaces only ("166.0 174.0 170.0") —
    without this guard those lines match the numbered-heading shape and climb
    the tree as top-level chapters (选择性必修三 vlm 实锤).
    """
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def _content_level(text: str, mineru_level: int) -> int | None:
    """Map a MinerU heading level to a tree level, dropping page furniture.

    Real K12 structure is unit(1) → lesson/chapter(2) → section(3), with 目
    (4) beneath sections. Practice headers (练习/习题/感受·理解…) and bare
    numeric labels are content furniture, not structure — demote to None so
    they stay body blocks.
    """
    if not text or _PRACTICE_RE.match(text) or _BARE_NUM_RE.match(text):
        return None
    # 栏目黑名单（判据 3）: 探究与分享/相关链接/思考与讨论 … are activity
    # columns MinerU tags as headings — never structure at ANY level.
    if _norm(text) in COLUMN_BLACKLIST:
        return None
    # Numbered exercise stems mis-tagged as headings ("5. 设 A 是一个集合…"):
    # a heading is short and carries no sentence punctuation. The stem number
    # is followed by a NON-digit ("5. 设…"); a section number is "6.1" — the
    # second group is digits, and what follows is the section's own name.
    if re.match(r"^\d{1,3}[.、．](?!\d)\s*\S", text) and (len(text) > 12 or re.search(r"[。？?！!,，]", text)):
        return None
    # Three-part sub-section numbering ("6.2.1 排列") is a 目 under 节 6.2
    # regardless of the MinerU level (人教A数学 flattens it to level 2);
    # depth of the numbering, not the level, is the structural tell.
    m = _SUB_SUB_NUM_RE.match(text)
    if m and _has_cjk(m.group(1)) and len(text) <= 30 and not _MU_SENTENCE_RE.search(text):
        return 4
    if _PART_RE.match(text) or _UNIT_RE.match(text):
        return 1
    if _LESSON_RE.match(text) or _CHAPTER_NUM_RE.match(text):
        return 2
    if _FRAME_RE.match(text) or (_TITLED_SECTION_RE.match(text) and _has_cjk(text)):
        return 3
    # Unnumbered headings deeper than the 节 level are 目 (统编政史地/语文:
    # 单元→课→目, MinerU tags the 目 level 3+). 判据 1.
    if mineru_level >= 3 and _content_level_mu_ok(text):
        return 4
    # MinerU L1 on the cover ("普通高中教科书") is book furniture when it
    # appears before any real unit; treat generic L1 as lesson-level.
    return 2 if mineru_level <= 2 else 3


def stable_node_id(doc_id: str, struct_path: str) -> str:
    """Stable 12-hex id for a textbook-tree node.

    ``sha1(doc_id + '|' + struct_path)[:12]`` — same ``(doc_id, struct_path)``
    pair always yields the same id (re-index stable), different docs or paths
    collide only by hash chance. ``struct_path`` is the "/"-joined heading
    chain of the node, which is already the natural content identity.
    """
    return hashlib.sha1(f"{doc_id}|{struct_path}".encode("utf-8")).hexdigest()[:12]


@dataclass
class StructureNode:
    title: str
    level: int
    children: list["StructureNode"] = field(default_factory=list)
    # Flat index of block positions covered by this node (end-exclusive).
    block_span: tuple[int, int] = (0, 0)
    # "/"-joined heading chain (``第1章 集合/1.1 集合的概念``) and its stable
    # node id (see :func:`stable_node_id`). Both are the bridge between the
    # textbook tree and knowledge points / questions derived from this node.
    struct_path: str = ""
    node_id: str = ""
    # 印刷页码（页脚 page_number 直读；None=页脚法未覆盖该课）。随 to_dict 落盘，
    # 供阅读器"跳教材页"与坐标校验使用。
    printed_page: int | None = None
    # Node kind: "mu" for level-4 目 nodes (KP granularity); "" for the
    # structural levels 1-3. Serialized only when set so the textbook-tree
    # API keeps its existing shape for older levels.
    node_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = {
            "title": self.title,
            "level": self.level,
            "struct_path": self.struct_path,
            "node_id": self.node_id,
            "children": [c.to_dict() for c in self.children],
        }
        if self.node_type:
            d["type"] = self.node_type
        if self.printed_page is not None:
            d["printed_page"] = self.printed_page
        return d


def build_tree(blocks: list[dict], *, text_fn=_block_text_v1, doc_id: str = "") -> tuple[dict | None, list[str]]:
    """Return ``(tree_dict_or_None, per_block_struct_paths)``.

    ``per_block_struct_paths[i]`` is the ``a/b/c`` path of block *i* ("" for
    pre-heading front matter). The tree keeps unit/lesson/section levels
    (1-3) plus 目 nodes (level 4, ``type: "mu"``) — the KP granularity
    beneath sections. Every tree node carries its ``struct_path`` and a
    stable ``node_id`` derived from ``doc_id`` (see :func:`stable_node_id`);
    pass the same ``doc_id`` (e.g. the file path) across re-indexes so node
    ids survive.
    """
    paths: list[str] = []
    stack: list[tuple[int, str]] = []  # (level, title) — current heading chain
    root_children: list[StructureNode] = []
    node_stack: list[StructureNode] = []
    doc_title = ""
    # TOC-page skip: once a "目录" heading is seen, swallow headings (and
    # dotted-entry lines) until real content resumes — detected by a heading
    # that re-appears after the TOC's own entries, i.e. the first *titled*
    # unit/lesson after at least one TOC entry. K12 convention: the TOC lists
    # chapters in order and the body restarts at chapter 1.
    in_toc = False
    toc_seen_entries = 0

    # ── Running-header lesson register (页脚法, v0.4) ────────────────────
    # Footer blocks repeat the current chapter title with the printed page
    # number nearby. They form an authoritative lesson register: when present,
    # a lesson-level title block may only open a lesson if the running headers
    # also announce it — otherwise it is TOC-page noise / a false heading
    # (必修1: body lesson headings were dropped to discarded_blocks, leaving
    # only TOC rows as title blocks, which fabricated wrong chapter starts).
    footer_register: dict[str, int | None] = {}  # squashed_title -> printed_page
    last_page_number: int | None = None
    for block in blocks:
        btype = block.get("type")
        text = text_fn(block)
        if btype == "page_number" and text.isdigit():
            last_page_number = int(text)
        elif btype == "footer" and re.match(
            r"^第\s*[一二三四五六七八九十百\d]+\s*(?:课|章)", text
        ):
            footer_register[_squash(text)] = last_page_number
    footer_register_active = bool(footer_register)

    # 多档判级（P6）: 低层级压平书按触发互斥选档（政治 → 历史/地理 → 语文
    # → 英语），判级走各档专用分支；未命中任何档的书走 B2-a 原生判据。
    tier = _select_flat_tier(blocks, text_fn)
    flat = bool(tier)
    politics = tier == "politics"
    history_geo = tier == "history_geo"
    yuwen = tier == "yuwen"
    yingyu = tier == "yingyu"
    politics_register: frozenset[str] = (
        _politics_toc_register(blocks, text_fn) if politics else frozenset()
    )
    history_geo_register: frozenset[str] = (
        _history_geo_toc_register(blocks, text_fn) if history_geo else frozenset()
    )
    yuwen_register: frozenset[str] = _yuwen_toc_register(blocks, text_fn) if yuwen else frozenset()
    yingyu_register: frozenset[str] = (
        _yingyu_unit_register(blocks, text_fn) if yingyu else frozenset()
    )
    # 历史/地理：页脚登记的课名去掉课号后的后缀表——正文里丢了"第N课"前缀
    # 的课标题（纲要上第5课压成 lvl1 裸题名）靠后缀回认，避免其目级错挂。
    lesson_suffixes: frozenset[str] = frozenset()
    if history_geo:
        suffixes = set()
        for key in footer_register:
            m = re.match(r"^第\s*[一二三四五六七八九十百\d]+\s*课(.+)$", key)
            if m and len(m.group(1)) >= 4:
                suffixes.add(m.group(1))
        lesson_suffixes = frozenset(suffixes)
    # 历史/地理：上一块是栏目块（学习聚焦/史料阅读…）时，紧邻的标题块是
    # 栏目内容标题，不是目。
    prev_column = False

    for i, block in enumerate(blocks):
        text = text_fn(block)
        # Running headers / page furniture never open structure (MinerU types
        # them "header"/"footer"/"page_number"; some engines leave them as
        # plain text with heading levels — those are exact chapter-name repeats).
        if block.get("type") in ("header", "footer", "page_number"):
            paths.append("".join(f"/{t}" for _, t in stack))
            continue
        if politics:
            level = _politics_level(block, text, politics_register)
        elif history_geo:
            level = _history_geo_level(
                block, text, prev_column, history_geo_register, lesson_suffixes
            )
        elif yuwen:
            level = _yuwen_level(block, text, yuwen_register)
        elif yingyu:
            level = _yingyu_level(block, text, yingyu_register)
        else:
            level = _block_level(block, text) if text else None
        if history_geo:
            prev_column = (
                block.get("text_level") in (1, 2)
                and text != ""
                and _is_history_geo_column(text)
            )
        # 压平档封面书名：lvl1 只剩封面/出版页，取书前部最后一个 lvl1 作书名。
        if flat and i < 10 and block.get("text_level") == 1 and text and _has_cjk(text):
            doc_title = text
        # Chapter-name repeat with the SAME level and title as the current
        # chapter = running header noise, not a new structural node.
        if level is not None and stack and stack[-1][0] == level and stack[-1][1] == text:
            paths.append("/".join(t for _, t in stack))
            continue

        # TOC page handling.
        if text and re.match(r"^目\s*录$", text):
            in_toc = True
            toc_seen_entries = 0
            paths.append("")
            continue
        if in_toc:
            # Dotted/leader entries ("1.1 集合 …… 5") or bare chapter stubs.
            # 压平档目录行没有引导点，以"目名+页码"收尾（必修3 实锤）——
            # 不计数则 toc_seen_entries 恒为 0，正文第一章永远出不了目录。
            # 英语目录行是"Unit 1 The mass media.... 1"整块多行，点引导在块中
            # 间，不锚定行尾（译林教师用书实锤）。
            if text and (
                re.search(r"[.．…]{2,}\s*\d+$", text)
                or re.match(r"^(\d+[.．]\d*\s*\S{0,20}?)\s*…", text)
                or (flat and re.search(r"[一-鿿]\s*\d+\s*$", text))
                or (flat and re.search(r"[.．…]{2,}\s*\d+", text))
            ):
                toc_seen_entries += 1
                paths.append("")
                continue
            # TOC chapter lines are headings WITHOUT page leaders; the body's
            # first chapter is also a heading. Distinguish by chapter number
            # restart: the TOC ends where a heading's chapter number drops
            # back to the document's first chapter (K12 convention: body
            # restarts at chapter 1 / unit 1). 选必册 restart at 第六章+，so a
            # footer-registered title is the authoritative exit: running
            # headers only appear in the body's own pages, never on the TOC.
            if level is not None:
                m = re.match(r"^第\s*(\d+)\s*章", text) or re.match(
                    r"^第\s*([一二三四五六七八九十]+)\s*(?:单元|课|章)", text
                )
                if m and toc_seen_entries >= 1:
                    cn = m.group(1)
                    # 压平档的目录行本身就是 单元/课/章 形态标题（地理必修一
                    # 目录"第二章地球上的大气" lvl2 实锤），且章名会进页脚
                    # 登记表——压平档只认编号重启出目录，不走页脚逃生门。
                    if cn in ("1", "一") or (not flat and _squash(text) in footer_register):
                        in_toc = False  # body chapter heading — fall through
                    else:
                        paths.append("")
                        continue
                elif not m and footer_register_active and _squash(text) in footer_register:
                    in_toc = False  # registered lesson/section resumes — fall through
                elif not m and flat and toc_seen_entries >= 1 and block.get("text_level") == 1:
                    # 压平档的目录页不带 lvl1 标题（封面/出版页都在目录之前）；
                    # 目录后第一个 lvl1 是正文顶格标题（英语教师用书 "The mass
                    # media" 实锤）——出目录。
                    in_toc = False  # fall through
                else:
                    paths.append("")
                    continue
            else:
                paths.append("")
                continue
        # Skip book furniture headings entirely (cover, TOC page itself).
        if text and _BOOK_FURNITURE_RE.match(text) and len(stack) == 0 and i < 60:
            paths.append("")
            continue

        if level is not None and level <= 4:
            if level == 4 and not node_stack:
                # A 目 with no open 课/节 to hang under: no parent, no node
                # (判据 4 — 目节点挂在最近的节之下; bare-root mus are noise).
                paths.append("")
                continue
            if not doc_title and level == 1 and i < 3:
                doc_title = text  # first top heading ~ document title
            if level == 4:
                # Bold-marked mus carry markdown markers; the tree stores the
                # clean 目名 so paths/ids are marker-free. 压平档的目名还带
                # 首尾装饰符（"| 严格执法 |"）或角标（"沁园春·长沙①"），一并剥掉。
                text, _ = _strip_bold(text)
                if tier in ("politics", "history_geo"):
                    text = _politics_deco(text)
                elif yuwen:
                    text = _politics_deco(_YUWEN_ANNOT_RE.sub("", _YUWEN_SUP_RE.sub("", text)))
            # Pop deeper/equal levels from BOTH stacks in lockstep (the bug
            # before: node_stack only popped when non-empty, desyncing it and
            # re-parenting later chapters under stale nodes).
            while stack and stack[-1][0] >= level:
                stack.pop()
            while node_stack and node_stack[-1].level >= level:
                node_stack.pop()
            if (
                level == 4
                and history_geo
                and node_stack
                and sum(1 for c in node_stack[-1].children if c.level == 4)
                >= _HISTORY_GEO_MU_PER_PARENT
            ):
                # 每课/节目数上限（见 _HISTORY_GEO_MU_PER_PARENT 注）：超额
                # 目候选退回正文块，随当前课的路径走。
                paths.append("/".join(t for _, t in stack))
                continue
            # 页脚法门卫：running headers 已登记课/章名时，只有登记在册的
            # 课/章标题才能开课——目录页行/伪标题在此被拒（必修1 v0.1 假阳性
            # 的解）。按标题模式把关而非层级，兼容课制与章制教材。
            if footer_register_active and re.match(
                r"^第\s*[一二三四五六七八九十百\d]+\s*(?:课|章)", text
            ):
                if _squash(text) not in footer_register:
                    paths.append("")
                    continue
            stack.append((level, text))
            struct_path = "/".join(t for _, t in stack)
            node = StructureNode(
                title=text,
                level=level,
                block_span=(i, i + 1),
                struct_path=struct_path,
                node_id=stable_node_id(doc_id, struct_path),
                printed_page=footer_register.get(_squash(text)),
                node_type="mu" if level == 4 else "",
            )
            if node_stack:
                node_stack[-1].children.append(node)
            else:
                root_children.append(node)
            node_stack.append(node)
            paths.append(struct_path)
            continue

        # Body block: inherit current chain.
        paths.append("/".join(t for _, t in stack))
        if node_stack:
            s, _ = node_stack[-1].block_span
            node_stack[-1].block_span = (s, i + 1)

    tree = None
    if root_children:
        tree = {"title": doc_title, "children": [n.to_dict() for n in root_children]}
    return tree, paths
