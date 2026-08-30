"""Four-layer chapter rebuild from MinerU ``layout.json``.

Layers (all deterministic, no LLM):
  0. column blacklist   — 栏目名 never chapters (closed vocabulary)
  1. regex              — ``第X课/章/节/单元`` headings (works regardless of height)
  2. position filter    — heading must sit in the page-top band (y0 < top)
  3. adjacent merge     — same-page title blocks split by line-wrap rejoin
  4. TOC cross-check    — printed TOC page entries validate hit rate

Input is the MinerU layout dict (``layout["pdf_info"]``), one page object per
page with ``page_idx`` / ``para_blocks``; blocks carry ``type`` / ``bbox`` /
``lines[].spans[].content``. Spec: P0-章节重建引擎实施方案 §4.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .column_blacklist import COLUMN_BLACKLIST

CHAPTER_RE = re.compile(r"^第\s*[一二三四五六七八九十百\d]+\s*[课章节单元]")

# Title blocks whose text is shorter than this are layout noise (figure
# captions etc.) — never chapters even if they regex-match.
MIN_TITLE_CHARS = 4


@dataclass
class Chapter:
    """One rebuilt chapter: title + start page (0-based) + bbox on that page."""

    title: str
    page_idx: int
    bbox: list[float]
    end_page_idx: int | None = None  # filled by assign_page_ranges
    level: int = 1  # reserved: 1=课/章, 2=框/节 (from TOC cross-check)
    meta: dict = field(default_factory=dict)


def block_text(block: dict) -> str:
    return "".join(
        span.get("content", "")
        for line in block.get("lines", [])
        for span in line.get("spans", [])
    ).strip()


def merge_adjacent(blocks: list[dict], gap: float = 40.0) -> list[dict]:
    """Rejoin same-page title blocks split by line-wrap (counterexample #1).

    Sort by y0; vertical gap < ``gap`` with x-overlap merges into the first
    block (bbox union, text concat). Mutates nothing — returns new dicts.
    """
    merged: list[dict] = []
    for block in sorted(blocks, key=lambda b: b["bbox"][1]):
        if merged:
            prev = merged[-1]
            vgap = block["bbox"][1] - prev["bbox"][3]
            x_overlap = min(block["bbox"][2], prev["bbox"][2]) - max(
                block["bbox"][0], prev["bbox"][0]
            )
            if vgap < gap and x_overlap > 0:
                prev["bbox"] = [
                    min(prev["bbox"][0], block["bbox"][0]),
                    min(prev["bbox"][1], block["bbox"][1]),
                    max(prev["bbox"][2], block["bbox"][2]),
                    max(prev["bbox"][3], block["bbox"][3]),
                ]
                prev["text"] = (prev.get("text", "") + block_text(block)).strip()
                continue
        item = dict(block)
        item["text"] = block_text(block)
        merged.append(item)
    return merged


def rebuild(
    layout: dict,
    *,
    top_band: float = 120.0,
    merge_gap: float = 40.0,
) -> list[Chapter]:
    """Run the four-layer pipeline over ``layout`` and return chapter starts."""
    chapters: list[Chapter] = []
    for page in layout.get("pdf_info", []):
        title_blocks = [b for b in page.get("para_blocks", []) if b.get("type") == "title"]
        for block in merge_adjacent(title_blocks, gap=merge_gap):
            text = block.get("text", "")
            if text in COLUMN_BLACKLIST:  # layer 0 — closed column names
                continue
            if not CHAPTER_RE.match(text):  # layer 1 — regex first
                continue
            if len(text.replace(" ", "")) < MIN_TITLE_CHARS:
                continue
            if block["bbox"][1] >= top_band:  # layer 2 — page-top band
                continue
            chapters.append(
                Chapter(
                    title=text,
                    page_idx=page["page_idx"],
                    bbox=list(block["bbox"]),
                )
            )
    chapters = _dedupe_keep_last(chapters)  # counterexample #4: TOC page duplicates body
    return assign_page_ranges(chapters, page_count=len(layout.get("pdf_info", [])))


def _dedupe_keep_last(chapters: list[Chapter]) -> list[Chapter]:
    """Same title appearing on TOC page AND in the body: keep the body hit."""
    by_title: dict[str, Chapter] = {}
    order: list[str] = []
    for chapter in chapters:
        if chapter.title in by_title:
            by_title[chapter.title] = chapter  # later (higher page) wins
        else:
            by_title[chapter.title] = chapter
            order.append(chapter.title)
    return [by_title[t] for t in order]


def assign_page_ranges(chapters: list[Chapter], *, page_count: int) -> list[Chapter]:
    """Chapter i spans [start_i, start_{i+1}); the last runs to the last page."""
    for i, chapter in enumerate(chapters):
        chapter.end_page_idx = (
            chapters[i + 1].page_idx if i + 1 < len(chapters) else max(page_count - 1, chapter.page_idx)
        )
    return chapters
