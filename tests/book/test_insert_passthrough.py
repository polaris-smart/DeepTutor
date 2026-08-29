"""insert_block zero-LLM passthrough: user_note stays, reading joins the canon path."""

from __future__ import annotations

import pytest

from deeptutor.book.engine import BookEngine
from deeptutor.book.models import BlockStatus, BlockType, Book, Chapter, Page, Spine


class _Storage:
    def __init__(self, page: Page, book: Book, spine: Spine) -> None:
        self.page = page
        self.book = book
        self.spine = spine
        self.logs: list[str] = []

    def load_page(self, book_id: str, page_id: str) -> Page | None:
        return self.page if page_id == self.page.id else None

    def save_page(self, page: Page) -> None:
        self.page = page

    def load_book(self, book_id: str) -> Book | None:
        return self.book

    def load_spine(self, book_id: str):
        return self.spine

    def append_log(self, book_id: str, message: str, op: str = "info") -> None:
        self.logs.append(op)


def _engine(page: Page) -> tuple[BookEngine, _Storage]:
    book = Book(id="bk", title="书", language="zh", knowledge_bases=["政治"])
    spine = Spine(book_id="bk", chapters=[Chapter(id="ch_1", title="第一课", order=0)])
    storage = _Storage(page, book, spine)
    engine = BookEngine.__new__(BookEngine)
    engine.storage = storage
    from deeptutor.book.compiler import BookCompiler, CompilerOptions

    engine.compiler = BookCompiler.__new__(BookCompiler)
    engine.compiler.options = CompilerOptions()
    return engine, storage


@pytest.mark.asyncio
async def test_insert_reading_with_compile_now_false_is_ready_verbatim() -> None:
    page = Page(id="pg_1", book_id="bk", chapter_id="ch_1")
    engine, storage = _engine(page)

    block = await engine.insert_block(
        book_id="bk",
        page_id="pg_1",
        block_type=BlockType.READING,
        params={"body": "教材原文：社会主义发展史……", "variant": "activity"},
        compile_now=False,
    )

    assert block.status == BlockStatus.READY
    assert block.payload["body"] == "教材原文：社会主义发展史……"
    assert block.payload["variant"] == "activity"
    assert block.payload["author"] == "textbook"
    # The block is persisted on the stored page.
    assert storage.page.blocks[-1].status == BlockStatus.READY


@pytest.mark.asyncio
async def test_insert_user_note_still_passthrough() -> None:
    page = Page(id="pg_1", book_id="bk", chapter_id="ch_1")
    engine, _storage = _engine(page)

    block = await engine.insert_block(
        book_id="bk",
        page_id="pg_1",
        block_type=BlockType.USER_NOTE,
        params={"body": "我的笔记"},
        compile_now=False,
    )

    assert block.status == BlockStatus.READY
    assert block.payload["author"] == "user"
    assert block.payload["body"] == "我的笔记"
