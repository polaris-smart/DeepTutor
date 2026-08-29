"""Router tests for the textbook-import endpoints (spine import, pages import)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from starlette.testclient import TestClient

from deeptutor.api.routers import book as book_router
from deeptutor.book.models import (
    Block,
    BlockStatus,
    BlockType,
    Book,
    Chapter,
    Page,
    Spine,
)
import deeptutor.book.storage as storage_module
from deeptutor.services.path_service import PathService


class _RecordingEngine:
    """Real storage + recorders for the engine calls the import endpoints make."""

    def __init__(self, storage: storage_module.BookStorage) -> None:
        self.storage = storage
        self.confirm_spine_calls: list[dict[str, Any]] = []

    def load_spine(self, book_id: str) -> Spine | None:
        return self.storage.load_spine(book_id)

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


def _new_client(tmp_path, monkeypatch) -> tuple[TestClient, storage_module.BookStorage, _RecordingEngine]:
    service = PathService(workspace_root=tmp_path / "data")
    monkeypatch.setattr(storage_module, "get_path_service", lambda: service)
    storage_module._storages.clear()
    storage = storage_module.get_book_storage()
    engine = _RecordingEngine(storage)

    monkeypatch.setattr(book_router, "get_book_engine", lambda: engine)

    app = FastAPI()
    app.include_router(book_router.router, prefix="/api/v1/book")
    return TestClient(app), storage, engine


def test_spine_import_normalizes_toc_and_delegates(tmp_path, monkeypatch) -> None:
    client, storage, engine = _new_client(tmp_path, monkeypatch)
    storage.save_book(Book(id="bk_imp", title="政必修1"))
    toc = [
        {"title": "第一课 社会主义从空想到科学、从理论到实践的发展"},
        {"title": "第二课 只有社会主义才能救中国", "children": [{"title": "第一框"}]},
    ]

    response = client.post(
        "/api/v1/book/books/bk_imp/spine/import",
        json={"book_id": "bk_imp", "toc": toc, "auto_compile": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(engine.confirm_spine_calls) == 1
    call = engine.confirm_spine_calls[0]
    assert call["auto_compile"] is False
    chapters = call["spine"].chapters
    assert [c.title for c in chapters] == [
        "第一课 社会主义从空想到科学、从理论到实践的发展",
        "第二课 只有社会主义才能救中国",
        "第一框",
    ]
    # The spine was persisted and echoed back.
    assert body["spine"]["chapters"][0]["title"].startswith("第一课")


def test_spine_import_rejects_invalid_toc(tmp_path, monkeypatch) -> None:
    client, _storage, _engine = _new_client(tmp_path, monkeypatch)
    response = client.post(
        "/api/v1/book/books/bk_imp/spine/import",
        json={"book_id": "bk_imp", "toc": [{"title": ""}]},
    )
    assert response.status_code == 400


def test_spine_import_rejects_book_id_mismatch(tmp_path, monkeypatch) -> None:
    client, _storage, _engine = _new_client(tmp_path, monkeypatch)
    response = client.post(
        "/api/v1/book/books/bk_a/spine/import",
        json={"book_id": "bk_b", "toc": [{"title": "第一课"}]},
    )
    assert response.status_code == 400


def test_pages_import_creates_pages_under_chapter_and_marks_ready(
    tmp_path, monkeypatch
) -> None:
    client, storage, engine = _new_client(tmp_path, monkeypatch)
    book_id = "bk_pages"
    storage.save_book(Book(id=book_id, title="政必修1"))
    spine = Spine(
        book_id=book_id,
        chapters=[Chapter(id="ch_1", title="第一课", order=0)],
    )
    storage.save_spine(spine)

    response = client.post(
        f"/api/v1/book/books/{book_id}/pages/import",
        json={
            "book_id": book_id,
            "chapter_id": "ch_1",
            "pages": [
                {
                    "title": "第一框 原始社会的解体和阶级社会的演进",
                    "blocks": [
                        {
                            "block_type": "reading",
                            "params": {"body": "教材原文第一段", "variant": "prose"},
                        },
                        {
                            "block_type": "reading",
                            "params": {"body": "【探究与分享】", "variant": "activity"},
                        },
                    ],
                }
            ],
        },
    )

    assert response.status_code == 200
    page_id = response.json()["pages"][0]["id"]
    page = storage.load_page(book_id, page_id)
    assert page is not None
    # Page is promoted to READY and attached to the chapter.
    assert page.status.value == "ready"
    assert page.chapter_id == "ch_1"
    assert page.id in _chapter_page_ids(storage, spine, "ch_1")
    # Blocks are verbatim READY (variant mapping is covered by the generator
    # unit test — the stub engine here builds plain payloads).
    assert [b.status for b in page.blocks] == [BlockStatus.READY, BlockStatus.READY]
    assert page.blocks[0].payload["body"] == "教材原文第一段"
    assert page.blocks[1].params["variant"] == "activity"


def _chapter_page_ids(storage, spine, chapter_id) -> set[str]:
    saved = storage.load_spine(spine.book_id)
    chapter = saved.chapter_by_id(chapter_id) if saved else None
    return set(chapter.page_ids or []) if chapter else set()


def test_pages_import_rejects_unknown_chapter(tmp_path, monkeypatch) -> None:
    client, storage, _engine = _new_client(tmp_path, monkeypatch)
    book_id = "bk_pages2"
    storage.save_book(Book(id=book_id, title="书"))
    storage.save_spine(Spine(book_id=book_id, chapters=[Chapter(id="ch_x", title="课")]))

    response = client.post(
        f"/api/v1/book/books/{book_id}/pages/import",
        json={
            "book_id": book_id,
            "chapter_id": "ch_missing",
            "pages": [{"title": "页", "blocks": []}],
        },
    )
    assert response.status_code == 404


def test_pages_import_unknown_block_type_is_400(tmp_path, monkeypatch) -> None:
    client, storage, _engine = _new_client(tmp_path, monkeypatch)
    book_id = "bk_pages3"
    storage.save_book(Book(id=book_id, title="书"))
    storage.save_spine(Spine(book_id=book_id, chapters=[Chapter(id="ch_y", title="课")]))

    response = client.post(
        f"/api/v1/book/books/{book_id}/pages/import",
        json={
            "book_id": book_id,
            "chapter_id": "ch_y",
            "pages": [{"title": "页", "blocks": [{"block_type": "nope", "params": {}}]}],
        },
    )
    assert response.status_code == 400
