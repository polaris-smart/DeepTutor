"""Router tests for the one-shot canonicalize endpoint.

``POST /books/canonicalize`` collapses create-book + spine-import +
pages-import into a single deterministic call so an orchestrator can turn a
parsed textbook into a canonical Book without an LLM in the loop.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from starlette.testclient import TestClient

from deeptutor.api.routers import book as book_router
from deeptutor.book.models import (
    Block,
    BlockStatus,
    Book,
    BookStatus,
    Spine,
)
import deeptutor.book.storage as storage_module
from deeptutor.services.path_service import PathService


class _RecordingEngine:
    """Real storage + recorders for the engine calls canonicalize makes."""

    def __init__(self, storage: storage_module.BookStorage) -> None:
        self.storage = storage
        self.confirm_spine_calls: list[dict[str, Any]] = []
        self.create_book_calls: list[dict[str, Any]] = []

    def load_spine(self, book_id: str) -> Spine | None:
        return self.storage.load_spine(book_id)

    async def create_book(self, **kwargs):  # pragma: no cover - must never run
        self.create_book_calls.append(kwargs)
        raise AssertionError("canonicalize must not invoke the IdeationAgent")

    async def confirm_spine(self, *, book_id, edited_spine, auto_compile=True, **kwargs):
        self.confirm_spine_calls.append(
            {"book_id": book_id, "spine": edited_spine, "auto_compile": auto_compile}
        )
        if edited_spine is not None:
            self.storage.save_spine(edited_spine)
        return []

    async def insert_block(
        self, *, book_id, page_id, block_type, params, position=None, compile_now=False, **kwargs
    ):
        page = self.storage.load_page(book_id, page_id)
        if page is None:
            return None
        block = Block(
            type=block_type,
            status=BlockStatus.READY,
            params=params,
            payload={"format": "markdown", "body": str(params.get("body") or "")},
        )
        page.blocks.append(block)
        self.storage.save_page(page)
        return block


def _new_client(tmp_path, monkeypatch):
    service = PathService(workspace_root=tmp_path / "data")
    monkeypatch.setattr(storage_module, "get_path_service", lambda: service)
    storage_module._storages.clear()
    storage = storage_module.get_book_storage()
    engine = _RecordingEngine(storage)

    monkeypatch.setattr(book_router, "get_book_engine", lambda: engine)

    app = FastAPI()
    app.include_router(book_router.router, prefix="/api/v1/book")
    return TestClient(app), storage, engine


_TOC = [
    {"title": "第一课 原始社会的解体", "children": [{"title": "第一框 人类社会的演进"}]},
    {"title": "第二课 只有社会主义才能救中国"},
]


def _chapters(storage, book_id):
    spine = storage.load_spine(book_id)
    return spine.chapters if spine else []


def test_canonicalize_creates_book_spine_and_pages_in_one_call(tmp_path, monkeypatch) -> None:
    client, storage, engine = _new_client(tmp_path, monkeypatch)

    response = client.post(
        "/api/v1/book/books/canonicalize",
        json={
            "title": "政治必修1",
            "toc": _TOC,
            "language": "zh",
            "chapters": [
                {
                    "chapter_index": 0,
                    "pages": [
                        {
                            "title": "第一框 人类社会的演进",
                            "blocks": [
                                {
                                    "block_type": "reading",
                                    "params": {"body": "教材原文第一段", "variant": "prose"},
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    book_id = body["book"]["id"]

    # The IdeationAgent never runs — the title is the caller's, verbatim.
    assert engine.create_book_calls == []
    assert body["book"]["title"] == "政治必修1"
    assert body["book"]["language"] == "zh"

    saved = storage.load_book(book_id)
    assert saved is not None and saved.title == "政治必修1"

    # Spine came from the TOC, flattened, in order.
    assert [c.title for c in _chapters(storage, book_id)] == [
        "第一课 原始社会的解体",
        "第一框 人类社会的演进",
        "第二课 只有社会主义才能救中国",
    ]
    assert engine.confirm_spine_calls[0]["auto_compile"] is False

    # Pages landed under the addressed chapter and are verbatim READY.
    assert body["pages_created"] == 1
    page_id = body["chapters"][0]["pages"][0]["id"]
    page = storage.load_page(book_id, page_id)
    assert page is not None
    assert page.status.value == "ready"
    assert page.chapter_id == _chapters(storage, book_id)[0].id
    assert page.blocks[0].payload["body"] == "教材原文第一段"


def test_canonicalize_addresses_chapter_by_title(tmp_path, monkeypatch) -> None:
    client, storage, _engine = _new_client(tmp_path, monkeypatch)

    response = client.post(
        "/api/v1/book/books/canonicalize",
        json={
            "title": "政治必修1",
            "toc": _TOC,
            "chapters": [
                {
                    "chapter_title": "第二课 只有社会主义才能救中国",
                    "pages": [{"title": "正文", "blocks": []}],
                }
            ],
        },
    )

    assert response.status_code == 200
    book_id = response.json()["book"]["id"]
    page_id = response.json()["chapters"][0]["pages"][0]["id"]
    page = storage.load_page(book_id, page_id)
    target = [c for c in _chapters(storage, book_id) if c.title.startswith("第二课")][0]
    assert page.chapter_id == target.id


def test_canonicalize_without_pages_still_builds_the_spine(tmp_path, monkeypatch) -> None:
    client, storage, _engine = _new_client(tmp_path, monkeypatch)

    response = client.post(
        "/api/v1/book/books/canonicalize",
        json={"title": "地理必修1", "toc": [{"title": "第一章 宇宙中的地球"}]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["pages_created"] == 0
    assert len(_chapters(storage, body["book"]["id"])) == 1


def test_canonicalize_accepts_a_mineru_layout_as_the_spine_source(tmp_path, monkeypatch) -> None:
    client, storage, _engine = _new_client(tmp_path, monkeypatch)
    # Running headers live in discarded_blocks: a footer carrying the lesson
    # title plus the printed page number (the 页脚法 rebuild criteria).
    layout = {
        "pdf_info": [
            {
                "page_idx": 0,
                "para_blocks": [],
                "discarded_blocks": [
                    {"type": "footer", "lines": [{"spans": [{"content": "第一课 走进哲学"}]}]},
                    {"type": "page_number", "lines": [{"spans": [{"content": "1"}]}]},
                ],
            },
            {
                "page_idx": 19,
                "para_blocks": [],
                "discarded_blocks": [
                    {"type": "footer", "lines": [{"spans": [{"content": "第二课 探究世界的本质"}]}]},
                    {"type": "page_number", "lines": [{"spans": [{"content": "15"}]}]},
                ],
            },
        ]
    }

    response = client.post(
        "/api/v1/book/books/canonicalize",
        json={"title": "哲学与文化", "layout": layout, "source": "layout_json"},
    )

    assert response.status_code == 200
    chapters = _chapters(storage, response.json()["book"]["id"])
    assert [c.title for c in chapters] == ["第一课 走进哲学", "第二课 探究世界的本质"]
    # printed_page survives the import so page anchoring stays available.
    assert [c.meta["printed_page"] for c in chapters] == [1, 15]


def test_canonicalize_rejects_a_layout_without_running_headers(tmp_path, monkeypatch) -> None:
    client, _storage, _engine = _new_client(tmp_path, monkeypatch)
    response = client.post(
        "/api/v1/book/books/canonicalize",
        json={
            "title": "哲学与文化",
            "layout": {"pdf_info": [{"page_idx": 0, "para_blocks": [], "discarded_blocks": []}]},
            "source": "layout_json",
        },
    )
    assert response.status_code == 400


def test_canonicalize_requires_a_title(tmp_path, monkeypatch) -> None:
    client, _storage, _engine = _new_client(tmp_path, monkeypatch)
    response = client.post(
        "/api/v1/book/books/canonicalize",
        json={"title": "   ", "toc": [{"title": "第一课"}]},
    )
    assert response.status_code == 400


def test_canonicalize_rejects_an_invalid_toc(tmp_path, monkeypatch) -> None:
    client, _storage, _engine = _new_client(tmp_path, monkeypatch)
    response = client.post(
        "/api/v1/book/books/canonicalize",
        json={"title": "书", "toc": [{"title": ""}]},
    )
    assert response.status_code == 400


def test_canonicalize_rejects_an_unknown_chapter_reference(tmp_path, monkeypatch) -> None:
    client, _storage, _engine = _new_client(tmp_path, monkeypatch)
    response = client.post(
        "/api/v1/book/books/canonicalize",
        json={
            "title": "书",
            "toc": [{"title": "第一课"}],
            "chapters": [{"chapter_title": "不存在的课", "pages": [{"title": "页", "blocks": []}]}],
        },
    )
    assert response.status_code == 404


def test_canonicalize_marks_the_book_ready_when_pages_landed(tmp_path, monkeypatch) -> None:
    client, storage, _engine = _new_client(tmp_path, monkeypatch)

    response = client.post(
        "/api/v1/book/books/canonicalize",
        json={
            "title": "历史必修1",
            "toc": [{"title": "第一课 中华文明的起源"}],
            "chapters": [
                {
                    "chapter_index": 0,
                    "pages": [
                        {
                            "title": "正文",
                            "blocks": [
                                {"block_type": "reading", "params": {"body": "原文"}}
                            ],
                        }
                    ],
                }
            ],
        },
    )

    assert response.status_code == 200
    book = storage.load_book(response.json()["book"]["id"])
    assert book is not None
    assert book.status == BookStatus.READY
    assert book.chapter_count == 1
    assert book.page_count == 1
