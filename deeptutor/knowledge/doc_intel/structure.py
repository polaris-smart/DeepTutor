"""Textbook structure tree extraction: 单元 → 课 → 节.

Signals, in priority order:
1. ``text_level`` on title/text blocks (MinerU v1 content_list) — level 1 is
   usually the unit/chapter, level 2 the lesson/section.
2. v2 ``title`` blocks with ``content.level``.
3. Heading regex fallback (第X单元 / 第X课 / X.X 节) when levels are absent
   (vlm products emit plain text blocks with levels only sometimes).

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


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


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
    if _TITLED_SECTION_RE.match(text) and len(text) <= 34 and not re.search(r"[。？?！!,，：:]", text):
        return 3
    return None


def _content_level(text: str, mineru_level: int) -> int | None:
    """Map a MinerU heading level to a tree level, dropping page furniture.

    Real K12 structure is unit(1) → lesson/chapter(2) → section(3). Practice
    headers (练习/习题/感受·理解…) and bare numeric labels are content
    furniture, not structure — demote to None so they stay body blocks.
    """
    if not text or _PRACTICE_RE.match(text) or _BARE_NUM_RE.match(text):
        return None
    # Numbered exercise stems mis-tagged as headings ("5. 设 A 是一个集合…"):
    # a heading is short and carries no sentence punctuation.
    if re.match(r"^\d{1,3}[.、．]\s*\S", text) and (len(text) > 12 or re.search(r"[。？?！!,，]", text)):
        return None
    if _PART_RE.match(text) or _UNIT_RE.match(text):
        return 1
    if _LESSON_RE.match(text) or _CHAPTER_NUM_RE.match(text):
        return 2
    if _FRAME_RE.match(text) or _TITLED_SECTION_RE.match(text):
        return 3
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

    def to_dict(self) -> dict[str, Any]:
        d = {
            "title": self.title,
            "level": self.level,
            "struct_path": self.struct_path,
            "node_id": self.node_id,
            "children": [c.to_dict() for c in self.children],
        }
        if self.printed_page is not None:
            d["printed_page"] = self.printed_page
        return d


def build_tree(blocks: list[dict], *, text_fn=_block_text_v1, doc_id: str = "") -> tuple[dict | None, list[str]]:
    """Return ``(tree_dict_or_None, per_block_struct_paths)``.

    ``per_block_struct_paths[i]`` is the ``a/b/c`` path of block *i* ("" for
    pre-heading front matter). The tree keeps only unit/lesson/section levels
    (1-3); deeper heading levels are folded into the path but not the tree.
    Every tree node carries its ``struct_path`` and a stable ``node_id``
    derived from ``doc_id`` (see :func:`stable_node_id`); pass the same
    ``doc_id`` (e.g. the file path) across re-indexes so node ids survive.
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
    footer_register: dict[str, int | None] = {}  # norm_title -> printed_page
    last_page_number: int | None = None
    for block in blocks:
        btype = block.get("type")
        text = text_fn(block)
        if btype == "page_number" and text.isdigit():
            last_page_number = int(text)
        elif btype == "footer" and re.match(
            r"^第\s*[一二三四五六七八九十百\d]+\s*课", text
        ):
            footer_register[_norm(text)] = last_page_number
    footer_register_active = bool(footer_register)

    for i, block in enumerate(blocks):
        text = text_fn(block)
        # Running headers / page furniture never open structure (MinerU types
        # them "header"/"footer"/"page_number"; some engines leave them as
        # plain text with heading levels — those are exact chapter-name repeats).
        if block.get("type") in ("header", "footer", "page_number"):
            paths.append("".join(f"/{t}" for _, t in stack))
            continue
        level = _block_level(block, text) if text else None
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
            if text and (re.search(r"[.．…]{2,}\s*\d+$", text) or re.match(r"^(\d+[.．]\d*\s*\S{0,20}?)\s*…", text)):
                toc_seen_entries += 1
                paths.append("")
                continue
            # TOC chapter lines are headings WITHOUT page leaders; the body's
            # first chapter is also a heading. Distinguish by chapter number
            # restart: the TOC ends where a heading's chapter number drops
            # back to the document's first chapter (K12 convention: body
            # restarts at chapter 1 / unit 1).
            if level is not None:
                m = re.match(r"^第\s*(\d+)\s*章", text) or re.match(r"^第\s*([一二三四五六七八九十]+)\s*(?:单元|课)", text)
                if m and toc_seen_entries >= 1:
                    cn = m.group(1)
                    if cn in ("1", "一"):
                        in_toc = False  # body chapter 1 heading — fall through
                    else:
                        paths.append("")
                        continue
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

        if level is not None and level <= 3:
            if not doc_title and level == 1 and i < 3:
                doc_title = text  # first top heading ~ document title
            # Pop deeper/equal levels from BOTH stacks in lockstep (the bug
            # before: node_stack only popped when non-empty, desyncing it and
            # re-parenting later chapters under stale nodes).
            while stack and stack[-1][0] >= level:
                stack.pop()
            while node_stack and node_stack[-1].level >= level:
                node_stack.pop()
            # 页脚法门卫：running headers 已登记课名时，只有登记在册的课标题
            # 才能开课——目录页行/伪标题在此被拒（必修1 v0.1 假阳性的解）。
            if footer_register_active and level == 2:
                if _norm(text) not in footer_register:
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
                printed_page=footer_register.get(_norm(text)),
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
