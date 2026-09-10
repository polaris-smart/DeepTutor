"""B2-b【2】canonical KP 树缓存：manifest.metadata 读写 helpers."""

from __future__ import annotations

from pathlib import Path

from deeptutor.book.models import Book
import deeptutor.book.storage as storage_module
from deeptutor.services.path_service import PathService

CANONICAL_TREE = {
    "title": "人教A数学选择性必修第三册",
    "children": [
        {
            "title": "第六章 计数原理",
            "level": 1,
            "struct_path": "第六章 计数原理",
            "node_id": "6a6a6a6a6a6a",
            "children": [
                {
                    "title": "6.2 排列与组合",
                    "level": 3,
                    "struct_path": "第六章 计数原理/6.2 排列与组合",
                    "node_id": "626262626262",
                    "children": [
                        {
                            "title": "6.2.1 排列",
                            "level": 4,
                            "struct_path": "第六章 计数原理/6.2 排列与组合/6.2.1 排列",
                            "node_id": "621621621621",
                            "children": [],
                            "type": "mu",
                        }
                    ],
                }
            ],
        }
    ],
}


def _build_storage(tmp_path: Path, monkeypatch) -> storage_module.BookStorage:
    service = PathService(workspace_root=tmp_path / "data")
    storage_module._storages.clear()
    monkeypatch.setattr(storage_module, "get_path_service", lambda: service)
    return storage_module.get_book_storage()


def test_canonical_tree_roundtrip_through_manifest(tmp_path: Path, monkeypatch) -> None:
    storage = _build_storage(tmp_path, monkeypatch)
    storage.save_book(Book(id="bk_canon", title="数学选必三"))

    assert storage.save_canonical_kp_tree("bk_canon", CANONICAL_TREE) is True

    loaded = storage.load_canonical_kp_tree("bk_canon")
    assert loaded == CANONICAL_TREE

    # Stored inside manifest metadata, and the Book model still loads it.
    manifest = storage.load_book("bk_canon")
    assert manifest is not None
    assert manifest.metadata["canonical_kp_tree"] == CANONICAL_TREE

    # A later manifest save must not drop the cached tree.
    manifest.title = "改名不丢缓存"
    storage.save_book(manifest)
    assert storage.load_canonical_kp_tree("bk_canon") == CANONICAL_TREE


def test_canonical_tree_missing_manifest_and_empty_cases(tmp_path: Path, monkeypatch) -> None:
    storage = _build_storage(tmp_path, monkeypatch)

    # No book at all: save refuses (never fabricates a book), load is None.
    assert storage.save_canonical_kp_tree("bk_absent", CANONICAL_TREE) is False
    assert storage.load_canonical_kp_tree("bk_absent") is None

    # Existing book without a cache: load is None (mechanical fallback path).
    storage.save_book(Book(id="bk_nocache"))
    assert storage.load_canonical_kp_tree("bk_nocache") is None
