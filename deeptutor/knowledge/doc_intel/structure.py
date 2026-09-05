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
    """政治档触发：max(text_level)≤2 且 lvl2 中 第N课 形态标题 ≥3。

    自动检测、不认书名：数学/物理书的标题层级到 3 以上，或 课 标题不以
    lvl2 出现，都不触发。
    """
    lessons = 0
    max_level = 0
    for block in blocks:
        lvl = block.get("text_level")
        if isinstance(lvl, int) and lvl > 0:
            if lvl > max_level:
                max_level = lvl
            if lvl == 2 and _POLITICS_LESSON_RE.match(_squash(text_fn(block))):
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

    # 政治档（P5）: 全书标题压平在 text_level 2 的课制书，判级走专用分支。
    politics = _is_politics_book(blocks, text_fn)
    politics_register: frozenset[str] = (
        _politics_toc_register(blocks, text_fn) if politics else frozenset()
    )

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
        else:
            level = _block_level(block, text) if text else None
        # 政治档封面书名：lvl1 只剩封面/出版页，取书前部最后一个 lvl1 作书名。
        if politics and i < 10 and block.get("text_level") == 1 and text and _has_cjk(text):
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
            # 政治档目录行没有引导点，以"目名+页码"收尾（必修3 实锤）——
            # 不计数则 toc_seen_entries 恒为 0，正文第一章永远出不了目录。
            if text and (
                re.search(r"[.．…]{2,}\s*\d+$", text)
                or re.match(r"^(\d+[.．]\d*\s*\S{0,20}?)\s*…", text)
                or (politics and re.search(r"[一-鿿]\s*\d+\s*$", text))
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
                m = re.match(r"^第\s*(\d+)\s*章", text) or re.match(r"^第\s*([一二三四五六七八九十]+)\s*(?:单元|课)", text)
                if m and toc_seen_entries >= 1:
                    cn = m.group(1)
                    if cn in ("1", "一") or _squash(text) in footer_register:
                        in_toc = False  # body chapter heading — fall through
                    else:
                        paths.append("")
                        continue
                elif not m and footer_register_active and _squash(text) in footer_register:
                    in_toc = False  # registered lesson/section resumes — fall through
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
                # clean 目名 so paths/ids are marker-free. 政治档的目名还带
                # 首尾装饰符（"| 严格执法 |"），一并剥掉。
                text, _ = _strip_bold(text)
                if politics:
                    text = _politics_deco(text)
            # Pop deeper/equal levels from BOTH stacks in lockstep (the bug
            # before: node_stack only popped when non-empty, desyncing it and
            # re-parenting later chapters under stale nodes).
            while stack and stack[-1][0] >= level:
                stack.pop()
            while node_stack and node_stack[-1].level >= level:
                node_stack.pop()
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
