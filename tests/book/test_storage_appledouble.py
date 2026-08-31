"""AppleDouble ("._") files from Mac copies must never break book reads."""

from __future__ import annotations

import json

from deeptutor.book.models import Page
from deeptutor.book.storage import BookStorage
from deeptutor.services.path_service import PathService


def test_list_pages_ignores_appledouble_files(tmp_path):
    service = PathService(workspace_root=tmp_path)
    storage = BookStorage(path_service=service)
    book_id = "bk_ad"
    pages_dir = service.get_book_pages_dir(book_id)
    pages_dir.mkdir(parents=True)
    good = {"id": "pg_1", "book_id": book_id, "chapter_id": "ch_1", "title": "正文",
            "order": 1, "created_at": 1.0, "updated_at": 1.0,
            "blocks": [], "content_type": "theory", "status": "ready"}
    (pages_dir / "pg_1.json").write_text(json.dumps(good), encoding="utf-8")
    # AppleDouble binary junk (not valid utf-8 json)
    (pages_dir / "._pg_1.json").write_bytes(b"\x00\x05\x16\x07\x00\x96\x81")
    (pages_dir / "._book_meta.json").write_bytes(b"\x00\x07\x16\x07\x00\x96")

    pages = storage.list_pages(book_id)
    assert [p.id for p in pages] == ["pg_1"]
