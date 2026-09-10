"""P1-B 存量书补产脚本：缺失检测 / dry-run 零写入 / 学科门控 / BlockSkipped 容错.

全部 mock 书页结构，不依赖真实生产数据、不调真实 LLM（生成器被替身替换）。
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

import pytest

from deeptutor.book.blocks.base import BlockSkipped, get_block_registry
from deeptutor.book.engine import BookEngine
from deeptutor.book.models import (
    Block,
    BlockStatus,
    BlockType,
    Book,
    Chapter,
    ContentType,
    Page,
    Spine,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _PROJECT_ROOT / "scripts" / "backfill_blocks.py"


@lru_cache(maxsize=1)
def _load_module():
    spec = importlib.util.spec_from_file_location("backfill_blocks_under_test", _MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop(spec.name, None)


# ─────────────────────────────────────────────────────────────────────────────
# mock 书页结构
# ─────────────────────────────────────────────────────────────────────────────


class FakeBookStorage:
    """只实现补产脚本会触到的读/写面；save_page 计数用于零写入断言。"""

    def __init__(
        self,
        books: list[Book],
        spines: list[Spine],
        pages: list[Page],
    ) -> None:
        self.books = {book.id: book for book in books}
        self.spines = {spine.book_id: spine for spine in spines}
        self.pages: dict[str, Page] = {page.id: page for page in pages}
        self.save_page_calls = 0
        self.logs: list[str] = []

    def list_book_ids(self) -> list[str]:
        return list(self.books)

    def load_book(self, book_id: str) -> Book | None:
        return self.books.get(book_id)

    def load_spine(self, book_id: str) -> Spine | None:
        return self.spines.get(book_id)

    def list_pages(self, book_id: str) -> list[Page]:
        return sorted(
            (p for p in self.pages.values() if p.book_id == book_id),
            key=lambda p: (p.order, p.created_at),
        )

    def load_page(self, book_id: str, page_id: str) -> Page | None:
        return self.pages.get(page_id)

    def save_page(self, page: Page) -> None:
        self.save_page_calls += 1
        self.pages[page.id] = page

    def append_log(self, book_id: str, message: str, op: str = "info") -> None:
        self.logs.append(message)


def _book(book_id: str, title: str, *, subject: str | None = None) -> Book:
    metadata = {"subject": subject} if subject else {}
    return Book(id=book_id, title=title, language="zh", metadata=metadata)


def _spine(book_id: str, chapter: Chapter) -> Spine:
    return Spine(book_id=book_id, chapters=[chapter])


def _reading_page(book_id: str, chapter: Chapter, *, extra_blocks: list[Block] | None = None):
    page = Page(
        id=f"pg-{chapter.id}",
        book_id=book_id,
        chapter_id=chapter.id,
        title=chapter.title,
        content_type=chapter.content_type,
        order=chapter.order,
    )
    page.blocks = [
        Block(
            type=BlockType.READING,
            status=BlockStatus.READY,
            payload={"body": "教材原文……"},
        ),
        *(extra_blocks or []),
    ]
    return page


def _engine(books, spines, pages) -> tuple[Any, FakeBookStorage]:
    storage = FakeBookStorage(books, spines, pages)
    engine = BookEngine(storage=storage)
    return engine, storage


_MATH_CHAPTER = Chapter(
    id="ch-math-1",
    title="数列的概念",
    content_type=ContentType.THEORY,
    summary="等差与等比数列。",
)
_HISTORY_CHAPTER = Chapter(
    id="ch-hist-1",
    title="中华文明的起源",
    content_type=ContentType.THEORY,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. 缺失检测：该补的列出、已有的不重复补
# ─────────────────────────────────────────────────────────────────────────────


def test_missing_blocks_listed_and_existing_not_duplicated() -> None:
    module = _load_module()
    book = _book("bk-1", "高中数学人教A版2019-选择性必修第二册", subject="数学")
    page = _reading_page(
        book.id,
        _MATH_CHAPTER,
        extra_blocks=[Block(type=BlockType.QUIZ, status=BlockStatus.READY)],
    )

    plan = module.plan_page(page, _MATH_CHAPTER, book)

    planned_types = [item.block_type for item in plan.planned]
    # 该页型模板应有的生成块被列出（去重，一种一次）。
    assert BlockType.FLASH_CARDS in planned_types
    assert BlockType.CALLOUT in planned_types
    assert BlockType.FIGURE in planned_types
    assert BlockType.RETRIEVAL_PRACTICE in planned_types
    assert BlockType.DEEP_DIVE in planned_types
    assert BlockType.ERROR_DIAGNOSIS in planned_types
    assert len(planned_types) == len(set(planned_types))
    # 已有的 QUIZ 不重复补；长文骨架不补。
    assert BlockType.QUIZ not in planned_types
    assert BlockType.SECTION not in planned_types
    assert "quiz: already_present" in plan.notes
    assert "section: prose_backbone" in plan.notes
    # 数学交互件已注册生成器（P1-C）：数学书计划里应正常可补，不再 no_generator。
    planned_values = {item.block_type for item in plan.planned}
    template_math = {
        bt for bt, _ in module._TEMPLATES_V2[ContentType.THEORY] if bt in module._MATH_BLOCK_TYPES
    }
    assert template_math, "THEORY 模板应含数学交互族"
    assert template_math <= planned_values
    assert all(f"{t.value}: no_generator" not in plan.notes for t in module._MATH_BLOCK_TYPES)


def test_page_with_everything_present_plans_nothing() -> None:
    module = _load_module()
    book = _book("bk-full", "某书", subject="数学")
    template_types = [bt for bt, _ in module._TEMPLATES_V2[ContentType.THEORY]]
    page = _reading_page(
        book.id,
        _MATH_CHAPTER,
        extra_blocks=[
            Block(type=bt, status=BlockStatus.READY)
            for bt in template_types + [BlockType.DEEP_DIVE, BlockType.ERROR_DIAGNOSIS]
        ],
    )

    plan = module.plan_page(page, _MATH_CHAPTER, book)

    assert plan.planned == []


@pytest.mark.parametrize(
    "setup,expected_skip",
    [
        ("no_template", "no_template"),
        ("subpage", "deep_dive_subpage"),
        ("uncompiled", "page_not_compiled"),
    ],
)
def test_page_level_skips(setup: str, expected_skip: str) -> None:
    module = _load_module()
    book = _book("bk-skip", "某书")
    chapter = Chapter(id="ch-s", title="章", content_type=ContentType.THEORY)
    if setup == "no_template":
        chapter = Chapter(id="ch-s", title="概览", content_type=ContentType.OVERVIEW)
    page = Page(id="pg-s", book_id=book.id, chapter_id=chapter.id, content_type=chapter.content_type)
    if setup == "uncompiled":
        page.blocks = [Block(type=BlockType.QUIZ, status=BlockStatus.PENDING)]
    if setup == "subpage":
        page.parent_page_id = "pg-parent"
        page.blocks = [Block(type=BlockType.READING, status=BlockStatus.READY)]

    plan = module.plan_page(page, chapter, book)

    assert plan.skip == expected_skip
    assert plan.planned == []


# ─────────────────────────────────────────────────────────────────────────────
# 2. dry-run 零写入
# ─────────────────────────────────────────────────────────────────────────────


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    module = _load_module()
    book = _book("bk-dry", "高中数学人教A版2019-选择性必修第二册", subject="数学")
    chapter = _MATH_CHAPTER
    page = _reading_page(book.id, chapter)
    engine, storage = _engine([book], [_spine(book.id, chapter)], [page])
    state_dir = tmp_path / "state"

    summary = asyncio.run(
        module.run_book(
            engine,
            book,
            dry_run=True,
            subject_override="",
            concurrency=2,
            max_retries=1,
            backoff_seconds=0.01,
            state_dir=state_dir,
            resume=False,
            limit_page=None,
            log=lambda _msg: None,
        )
    )

    # 页面未被写入，也没生成断点状态文件。
    assert storage.save_page_calls == 0
    assert len(page.blocks) == 1
    assert not state_dir.exists() or not any(state_dir.iterdir())
    assert summary["status"] == "dry_run"
    assert summary["blocks_planned"] > 0


def test_dry_run_state_not_polluting_real_run(tmp_path: Path) -> None:
    """dry-run 不落状态 ⇒ 正式跑时不会被"已完成"假象跳过。"""
    module = _load_module()
    book = _book("bk-resume", "某书")
    chapter = _MATH_CHAPTER
    page = _reading_page(book.id, chapter)
    engine, _storage = _engine([book], [_spine(book.id, chapter)], [page])

    asyncio.run(
        module.run_book(
            engine,
            book,
            dry_run=True,
            subject_override="",
            concurrency=1,
            max_retries=0,
            backoff_seconds=0.01,
            state_dir=tmp_path,
            resume=True,
            limit_page=None,
            log=lambda _msg: None,
        )
    )
    assert module.load_done_page_ids(tmp_path, book.id) == set()


# ─────────────────────────────────────────────────────────────────────────────
# 3. 学科门控：非数学书剔除交互族；错题三件 / deep_dive 各科均可产
# ─────────────────────────────────────────────────────────────────────────────


def test_math_book_passes_gate_and_history_book_is_gated() -> None:
    module = _load_module()
    math_book = _book("bk-math", "高中数学人教A版2019-选择性必修第二册", subject="数学")
    history_book = _book("bk-hist", "中外历史纲要上", subject="历史")

    assert module.math_blocks_allowed(_MATH_CHAPTER, math_book) is True
    assert module.math_blocks_allowed(_HISTORY_CHAPTER, history_book) is False

    math_plan = module.plan_page(_reading_page(math_book.id, _MATH_CHAPTER), _MATH_CHAPTER, math_book)
    hist_page = _reading_page(history_book.id, _HISTORY_CHAPTER)
    hist_plan = module.plan_page(hist_page, _HISTORY_CHAPTER, history_book)

    # 数学书：交互族通过门控（P1-C 后生成器已注册，应正常进计划）。
    assert "desmos: subject_gate" not in math_plan.notes
    assert any(item.block_type == BlockType.DESMOS for item in math_plan.planned)
    # 历史书：交互族被学科门控整族剔除（THEORY 模板含 desmos/geogebra/formula）。
    template_types = {bt.value for bt, _ in module._TEMPLATES_V2[ContentType.THEORY]}
    for blocked in ("desmos", "geogebra", "formula"):
        assert blocked in template_types
        assert f"{blocked}: subject_gate" in hist_plan.notes
    assert all(item.block_type not in module._MATH_BLOCK_TYPES for item in hist_plan.planned)
    # 错题三件与 deep_dive 各科均可产：历史书计划里照样在。
    hist_planned = [item.block_type for item in hist_plan.planned]
    assert BlockType.RETRIEVAL_PRACTICE in hist_planned
    assert BlockType.DEEP_DIVE in hist_planned
    assert BlockType.ERROR_DIAGNOSIS in hist_planned


def test_subject_override_gates_english_book() -> None:
    module = _load_module()
    # 书名无学科关键词、_subject_of 兜底"通用" → 无 override 时 fail-open 放行。
    english_book = _book("bk-eng", "学程一  指导手册")
    assert module.effective_subject(_MATH_CHAPTER, english_book) == ""
    assert module.math_blocks_allowed(_MATH_CHAPTER, english_book) is True
    # --subject 英语 后门控生效。
    assert module.math_blocks_allowed(_MATH_CHAPTER, english_book, subject_override="英语") is False


def test_resolve_subject_overrides_parses_global_and_per_book() -> None:
    module = _load_module()
    global_subject, per_book = module.resolve_subject_overrides(
        ["英语", "bk_123=数学", " ", "bk_456=历史"]
    )
    assert global_subject == "英语"
    assert per_book == {"bk_123": "数学", "bk_456": "历史"}


# ─────────────────────────────────────────────────────────────────────────────
# 4. BlockSkipped（无错题数据）不中断批次
# ─────────────────────────────────────────────────────────────────────────────


def _stub_all_generators(monkeypatch: pytest.MonkeyPatch, skip_error_diagnosis: bool) -> None:
    """把注册表里每个生成器的 ``_generate`` 换成确定性的替身（不触真实 LLM）。"""
    registry = get_block_registry()

    def make_ready():
        async def _generate(_ctx):
            return {"stub": True}, [], {}

        return _generate

    def make_skip():
        async def _generate(_ctx):
            raise BlockSkipped("No real error data recorded for this book; skipping error_diagnosis.")

        return _generate

    for block_type in registry.types():
        generator = registry.get(block_type)
        assert generator is not None
        if skip_error_diagnosis and block_type == BlockType.ERROR_DIAGNOSIS:
            monkeypatch.setattr(generator, "_generate", make_skip())
        else:
            monkeypatch.setattr(generator, "_generate", make_ready())


def test_block_skipped_does_not_break_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()
    _stub_all_generators(monkeypatch, skip_error_diagnosis=True)
    book = _book("bk-skip-block", "某书")
    chapter = _MATH_CHAPTER
    page = _reading_page(book.id, chapter)
    engine, storage = _engine([book], [_spine(book.id, chapter)], [page])

    summary = asyncio.run(
        module.run_book(
            engine,
            book,
            dry_run=False,
            subject_override="",
            concurrency=2,
            max_retries=1,
            backoff_seconds=0.01,
            state_dir=tmp_path,
            resume=False,
            limit_page=None,
            log=lambda _msg: None,
        )
    )

    assert summary["pages"] == 1
    # error_diagnosis 因无错题数据被静默跳过，其余块照常补齐。
    state_lines = (tmp_path / f"backfill-{book.id}.jsonl").read_text(encoding="utf-8").splitlines()
    record = json.loads(state_lines[-1])
    assert record["page_id"] == page.id
    assert "error_diagnosis" in record["skipped"]
    assert "error_diagnosis" not in record["inserted"]
    assert {"quiz", "callout", "figure", "flash_cards", "retrieval_practice", "deep_dive"} <= set(
        record["inserted"]
    )
    # 被跳过的块已被 compiler 从页面剪掉，不留空壳。
    stored_page = storage.pages[page.id]
    assert not any(block.type == BlockType.ERROR_DIAGNOSIS for block in stored_page.blocks)


def test_resume_skips_completed_pages(tmp_path: Path) -> None:
    module = _load_module()
    book = _book("bk-resume-2", "某书")
    chapter = _MATH_CHAPTER
    page = _reading_page(book.id, chapter)
    engine, storage = _engine([book], [_spine(book.id, chapter)], [page])
    (tmp_path / f"backfill-{book.id}.jsonl").write_text(
        json.dumps({"page_id": page.id, "inserted": ["quiz"]}) + "\n", encoding="utf-8"
    )

    asyncio.run(
        module.run_book(
            engine,
            book,
            dry_run=False,
            subject_override="",
            concurrency=1,
            max_retries=0,
            backoff_seconds=0.01,
            state_dir=tmp_path,
            resume=True,
            limit_page=None,
            log=lambda _msg: None,
        )
    )

    # 断点命中：零写入（页面没有任何新块落盘）。
    assert storage.save_page_calls == 0


def test_limit_page_caps_pages(tmp_path: Path) -> None:
    module = _load_module()
    book = _book("bk-limit", "某书")
    chapter = _MATH_CHAPTER
    pages = []
    for index in range(5):
        chapter_i = Chapter(id=f"ch-{index}", title=f"第{index}课", content_type=ContentType.THEORY)
        pages.append(_reading_page(book.id, chapter_i))
    spine = Spine(book_id=book.id, chapters=[Chapter(id=f"ch-{i}", title=f"第{i}课") for i in range(5)])
    engine, _storage = _engine([book], [spine], pages)

    summary = asyncio.run(
        module.run_book(
            engine,
            book,
            dry_run=True,
            subject_override="",
            concurrency=1,
            max_retries=0,
            backoff_seconds=0.01,
            state_dir=tmp_path,
            resume=False,
            limit_page=2,
            log=lambda _msg: None,
        )
    )

    assert summary["pages"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# 5. 无生产数据环境优雅降级
# ─────────────────────────────────────────────────────────────────────────────


class _EmptyEngine:
    storage = object()

    def list_books(self):  # noqa: D102 - stub
        return []


def test_main_reports_missing_data_gracefully(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    module = _load_module()
    import deeptutor.book.engine as engine_module

    monkeypatch.setattr(engine_module, "get_book_engine", lambda: _EmptyEngine())

    exit_code = module.main(["--all-ready"])

    assert exit_code == 0
    assert "未找到书数据" in capsys.readouterr().out


def test_main_reports_unknown_book_id(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    module = _load_module()
    import deeptutor.book.engine as engine_module

    book = _book("bk-real", "某书")
    chapter = _MATH_CHAPTER
    page = _reading_page(book.id, chapter)
    engine, _storage = _engine([book], [_spine(book.id, chapter)], [page])
    monkeypatch.setattr(engine_module, "get_book_engine", lambda: engine)

    exit_code = module.main(["--book-id", "bk-missing"])

    assert exit_code == 1
    assert "未找到书数据" in capsys.readouterr().out
