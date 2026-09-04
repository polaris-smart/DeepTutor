"""P0: canonical KP tree auto-cache on canonicalize.

The write half B2-b left open: when a parsed textbook is canonicalized, the
doc_intel 目级 tree from its source KBs lands in
``manifest.metadata.canonical_kp_tree`` so import-from-book stops falling
back to the chapter-level mechanical sketch.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from starlette.testclient import TestClient

import deeptutor.book.canonical_tree as canonical_tree
from deeptutor.api.routers import book as book_router
from tests.api.test_book_canonicalize import _TOC, _RecordingEngine, _new_client

#: doc_intel slim-tree shape, exactly what document_loader stores as
#: ``doc_tree`` metadata in the LlamaIndex docstore.
DOC_TREE = {
    "title": "数学选择性必修第三册",
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
                            "type": "mu",
                        }
                    ],
                }
            ],
        }
    ],
}

_CANONICAL = {
    "title": "数学选择性必修第三册",
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
                            "type": "mu",
                        }
                    ],
                }
            ],
        }
    ],
}


def _canonicalize(client: TestClient) -> Any:
    return client.post(
        "/api/v1/book/books/canonicalize",
        json={
            "title": "数学选必三",
            "toc": _TOC,
            "language": "zh",
            "knowledge_bases": ["数学KB"],
            "chapters": [
                {
                    "chapter_index": 0,
                    "pages": [
                        {
                            "title": "第一框",
                            "blocks": [
                                {
                                    "block_type": "reading",
                                    "params": {"body": "教材原文", "variant": "prose"},
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    )


def test_canonicalize_autocaches_canonical_kp_tree(tmp_path, monkeypatch) -> None:
    client, storage, _engine = _new_client(tmp_path, monkeypatch)
    monkeypatch.setattr(canonical_tree, "load_kb_doc_trees", lambda _kbs: [DOC_TREE])

    response = _canonicalize(client)

    assert response.status_code == 200
    book_id = response.json()["book"]["id"]
    manifest = storage.load_book(book_id)
    assert manifest is not None
    tree = manifest.metadata["canonical_kp_tree"]
    assert tree == _CANONICAL
    # Downstream shape: import-from-book must find mu leaves with bridges.
    mus = [n for n in tree["children"][0]["children"][0]["children"]]
    assert mus[0]["type"] == "mu" and mus[0]["node_id"] and mus[0]["struct_path"]


def test_no_manifest_or_no_tree_never_breaks_canonicalize(tmp_path, monkeypatch) -> None:
    """No usable doc_tree (and a loader that explodes): canonicalize still 200s,
    the book is created, and nothing is cached."""
    client, storage, _engine = _new_client(tmp_path, monkeypatch)

    def _boom(_kbs):
        raise RuntimeError("docstore exploded")

    monkeypatch.setattr(canonical_tree, "load_kb_doc_trees", _boom)
    response = _canonicalize(client)
    assert response.status_code == 200
    book_id = response.json()["book"]["id"]
    manifest = storage.load_book(book_id)
    assert manifest is not None  # pipeline continued, book exists
    assert "canonical_kp_tree" not in (manifest.metadata or {})

    # Direct best-effort contract: raising loader → False, no exception.
    assert canonical_tree.cache_canonical_tree_for_book("bk_x", ["kb"], storage=storage) is False
    # No manifest at all → False (save refuses, never fabricates a book).
    monkeypatch.setattr(canonical_tree, "load_kb_doc_trees", lambda _kbs: [DOC_TREE])
    assert canonical_tree.cache_canonical_tree_for_book("bk_absent", ["kb"], storage=storage) is False


def test_autocache_is_idempotent(tmp_path, monkeypatch) -> None:
    client, storage, _engine = _new_client(tmp_path, monkeypatch)
    monkeypatch.setattr(canonical_tree, "load_kb_doc_trees", lambda _kbs: [DOC_TREE])

    response = _canonicalize(client)
    book_id = response.json()["book"]["id"]
    first = storage.load_canonical_kp_tree(book_id)
    assert first == _CANONICAL

    # Same inputs again: same final state, no metadata drift/accumulation.
    assert canonical_tree.cache_canonical_tree_for_book(book_id, ["数学KB"], storage=storage) is True
    second = storage.load_canonical_kp_tree(book_id)
    assert second == first
    manifest = storage.load_book(book_id)
    assert list((manifest.metadata or {}).keys()).count("canonical_kp_tree") == 1


def test_canonical_tree_from_doc_trees_gates_and_merges() -> None:
    # Degraded tiers (no children / no mu bridges) are rejected outright.
    assert canonical_tree.canonical_tree_from_doc_trees(
        [{"title": "书", "chapters": ["第一课"]}]
    ) is None
    assert canonical_tree.canonical_tree_from_doc_trees([{"title": "书", "children": []}]) is None
    no_bridge = {
        "title": "书",
        "children": [{"title": "目", "level": 4, "type": "mu"}],
    }
    assert canonical_tree.canonical_tree_from_doc_trees([no_bridge]) is None

    # One valid root → verbatim; several → merged under a synthetic root.
    assert canonical_tree.canonical_tree_from_doc_trees([DOC_TREE]) == _CANONICAL
    merged = canonical_tree.canonical_tree_from_doc_trees([DOC_TREE, DOC_TREE])
    assert merged is not None
    assert merged["title"] == "" and len(merged["children"]) == 2
