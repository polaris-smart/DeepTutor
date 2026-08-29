"""Tests for the verbatim reading block generator (textbook canon channel)."""

from __future__ import annotations

import pytest

from deeptutor.book.blocks.base import BlockContext
from deeptutor.book.blocks.reading import ReadingGenerator
from deeptutor.book.models import Block, BlockStatus, BlockType, Chapter, Page


def _ctx(params: dict) -> BlockContext:
    chapter = Chapter(id="ch_1", title="第一课")
    page = Page(id="pg_1", book_id="bk", chapter_id="ch_1")
    block = Block(type=BlockType.READING, params=params)
    return BlockContext(book_id="bk", chapter=chapter, page=page, block=block, language="zh")


@pytest.mark.asyncio
async def test_reading_block_is_verbatim_passthrough() -> None:
    ctx = _ctx({"body": "教材原文：社会主义发展史……", "variant": "activity", "source_label": "必修1 P4"})
    block = await ReadingGenerator().generate(ctx)

    assert block.status == BlockStatus.READY
    assert block.payload["body"] == "教材原文：社会主义发展史……"
    assert block.payload["variant"] == "activity"
    assert block.payload["source_label"] == "必修1 P4"
    assert block.payload["author"] == "textbook"
    assert block.source_anchors == []


@pytest.mark.asyncio
async def test_reading_block_rejects_unknown_variant() -> None:
    ctx = _ctx({"body": "正文", "variant": "desmos-style"})
    block = await ReadingGenerator().generate(ctx)
    assert block.payload["variant"] == "prose"


@pytest.mark.asyncio
async def test_reading_block_defaults() -> None:
    ctx = _ctx({})
    block = await ReadingGenerator().generate(ctx)
    assert block.payload["body"] == ""
    assert block.payload["variant"] == "prose"
    assert block.payload["source_label"] == ""
