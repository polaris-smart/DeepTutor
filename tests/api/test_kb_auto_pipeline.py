"""P7【3】管线状态端点 + P7【2】ingest 尾部事件链.

``GET /knowledge-bases/{kb}/pipeline`` reads the per-file four-stage state
(raw/parsed/canonicalized/imported) the event chain wrote into the KB
manifest's ``metadata.pipeline``. The chain itself only fires when the KB
manifest carries ``auto_pipeline: true`` — the explicit switch keeps every
existing KB's upload behaviour byte-for-byte identical.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import importlib

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import deeptutor.api.routers.knowledge as knowledge_router
import deeptutor.book.canonical_tree as canonical_tree
import deeptutor.book.storage as storage_module
import deeptutor.knowledge.auto_pipeline as auto_pipeline
import deeptutor.knowledge.doc_intel.enrich as _enrich_pkg
import deeptutor.knowledge.doc_intel as _doc_intel

# The doc_intel package re-exports the ``enrich`` function, shadowing the
# submodule attribute — resolve the module explicitly for patching.
enrich_module = importlib.import_module("deeptutor.knowledge.doc_intel.enrich")
_ = (_enrich_pkg, _doc_intel)
from deeptutor.knowledge.manager import KnowledgeBaseManager
from deeptutor.services.path_service import PathService

KB = "教材-测试"

LAYOUT_TREE = {
    "title": "选择性必修3 逻辑与思维",
    "children": [
        {
            "title": "第一单元",
            "level": 1,
            "struct_path": "第一单元",
            "node_id": "u1u1u1u1u1u1",
            "children": [
                {
                    "title": "目1 思维",
                    "level": 4,
                    "struct_path": "第一单元/目1 思维",
                    "node_id": "m1m1m1m1m1m1",
                    "type": "mu",
                }
            ],
        }
    ],
}


def _register_kb(base_dir, name: str = KB, *, metadata: dict[str, Any] | None = None) -> None:
    manager = KnowledgeBaseManager(base_dir=str(base_dir))
    manager.update_kb_status(name=name, status="ready")
    if metadata is not None:
        manager.config = manager._load_config()
        manager.config["knowledge_bases"][name]["metadata"] = metadata
        manager._save_config()


@pytest.fixture
def kb_env(tmp_path, monkeypatch) -> Any:
    """A temp KB registry with the auto_pipeline flag on."""
    base_dir = tmp_path / "kbs"
    base_dir.mkdir()
    _register_kb(base_dir, metadata={"auto_pipeline": True})
    return base_dir


LAYOUT = {
    "pdf_info": [
        {
            "page_idx": 0,
            "para_blocks": [
                {"type": "title", "level": 1, "lines": [{"spans": [{"content": "第一单元"}]}]},
                {"type": "text", "lines": [{"spans": [{"content": "第一课的正文。"}]}]},
            ],
            "discarded_blocks": [
                {"type": "footer", "lines": [{"spans": [{"content": "第一课 走进哲学"}]}]},
                {"type": "page_number", "lines": [{"spans": [{"content": "1"}]}]},
            ],
        },
        {
            "page_idx": 1,
            "para_blocks": [
                {"type": "text", "lines": [{"spans": [{"content": "第二页正文。"}]}]},
            ],
            "discarded_blocks": [
                {"type": "footer", "lines": [{"spans": [{"content": "第二课 探究世界的本质"}]}]},
                {"type": "page_number", "lines": [{"spans": [{"content": "15"}]}]},
            ],
        },
    ]
}


def _seed_parse_dir(tmp_path) -> str:
    """A parse product dir: md + whole-book layout (页脚法 chapters + pages)."""
    product = tmp_path / "parse" / "书A"
    product.mkdir(parents=True)
    (product / "书A.md").write_text("教材原文", encoding="utf-8")
    (product / "layout.json").write_text(json.dumps(LAYOUT, ensure_ascii=False), encoding="utf-8")
    return str(product)


def _patch_chain(monkeypatch, *, books: list[dict[str, Any]] | None = None):
    """Stub parse (class seam) and return the injected canonicalize/import
    stubs the chain composes, recording every call."""
    calls: dict[str, list[Any]] = {"canonicalize": [], "import": [], "parse": []}
    holder: dict[str, str] = {"dir": ""}

    def fake_parse(self, source_path, **kwargs):
        calls["parse"].append(str(source_path))
        return SimpleNamespace(workdir=holder["dir"], engine="text_only")

    async def fake_canonicalize(request):
        calls["canonicalize"].append(request)
        if books is not None and not books:
            raise RuntimeError("canonicalize exploded")
        book = {"id": "book-1", "title": request.title, "chapter_count": 2, "page_count": 3}
        return {"book": book}

    async def fake_import_from_book(book_id, body):
        calls["import"].append(book_id)
        return {"status": "ok", "module_count": 4}

    monkeypatch.setattr("deeptutor.services.parsing.service.ParseService.parse", fake_parse)
    return calls, holder, {
        "canonicalize": (fake_canonicalize, SimpleNamespace),
        "import_from_book": fake_import_from_book,
        "import_request_model": SimpleNamespace,
    }


# ─────────────────────────────────────────────────────────────────────────────
# P7【3】状态端点
# ─────────────────────────────────────────────────────────────────────────────


def _pipeline_client(monkeypatch, base_dir) -> TestClient:
    manager = KnowledgeBaseManager(base_dir=str(base_dir))
    monkeypatch.setattr(knowledge_router, "get_kb_manager", lambda: manager)
    app = FastAPI()
    app.include_router(knowledge_router.router, prefix="/api")
    return TestClient(app)


def test_pipeline_endpoint_reports_four_stages_with_error(kb_env, monkeypatch) -> None:
    auto_pipeline.record_stage(KB, "书A.pdf", "raw", base_dir=kb_env, payload={"path": "/raw/书A.pdf"})
    auto_pipeline.record_stage(KB, "书A.pdf", "parsed", base_dir=kb_env, payload={"workdir": "/cache/x"})
    auto_pipeline.record_stage(
        KB, "书A.pdf", "canonicalized", base_dir=kb_env, payload={"book_id": "book-1", "source": "layout_json"}
    )
    auto_pipeline.record_stage(KB, "书A.pdf", "imported", base_dir=kb_env, error="no usable tree")

    client = _pipeline_client(monkeypatch, kb_env)
    response = client.get(f"/api/knowledge-bases/{KB}/pipeline")
    assert response.status_code == 200
    body = response.json()
    assert body["kb_name"] == KB and body["auto_pipeline"] is True
    assert len(body["files"]) == 1
    record = body["files"][0]
    assert record["file"] == "书A.pdf"
    assert record["status"] == "error"
    assert record["error"] == "no usable tree"
    # Succeeded stages stay visible; only imported is missing.
    assert set(record["stages"]) == {"raw", "parsed", "canonicalized"}
    assert record["stages"]["parsed"]["workdir"] == "/cache/x"
    assert record["stages"]["canonicalized"]["book_id"] == "book-1"


def test_pipeline_endpoint_404_and_empty_state(kb_env, monkeypatch) -> None:
    client = _pipeline_client(monkeypatch, kb_env)

    # A registered KB the chain never ran for: no files, flag reported as-is.
    _register_kb(kb_env, "教材-安静", metadata={"auto_pipeline": False})
    body = client.get("/api/knowledge-bases/教材-安静/pipeline").json()
    assert body["files"] == [] and body["auto_pipeline"] is False

    assert client.get("/api/knowledge-bases/不存在/pipeline").status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# P7【2】事件链
# ─────────────────────────────────────────────────────────────────────────────


def test_chain_runs_all_four_stages(kb_env, tmp_path, monkeypatch) -> None:
    calls, holder, stubs = _patch_chain(monkeypatch)
    holder["dir"] = _seed_parse_dir(tmp_path)
    pdf = kb_env / KB / "raw" / "书A.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4 fake")

    results = asyncio.run(
        auto_pipeline.run_auto_pipeline(KB, [str(pdf)], base_dir=kb_env, **stubs)
    )

    assert results["书A.pdf"] == {"status": "ok"}
    assert calls["parse"] == [str(pdf)]
    assert calls["canonicalize"][0].title == "书A"
    assert calls["canonicalize"][0].knowledge_bases == [KB]
    assert calls["import"] == ["book-1"]

    record = auto_pipeline.read_pipeline_state(KB, base_dir=kb_env)["书A.pdf"]
    assert record["status"] == "ok"
    assert set(record) >= {"raw", "parsed", "canonicalized", "imported"}
    assert record["imported"]["module_count"] == 4
    assert record["canonicalized"]["book_id"] == "book-1"


def test_chain_parse_failure_isolates_other_files(kb_env, tmp_path, monkeypatch) -> None:
    from deeptutor.services.parsing.types import ParserError

    calls, holder, stubs = _patch_chain(monkeypatch)
    holder["dir"] = _seed_parse_dir(tmp_path)
    real_parse = "deeptutor.services.parsing.service.ParseService.parse"

    def flaky_parse(self, source_path, **kwargs):
        if "bad" in str(source_path):
            calls["parse"].append(str(source_path))
            raise ParserError("engine not ready")
        return SimpleNamespace(workdir=holder["dir"], engine="text_only")

    monkeypatch.setattr(real_parse, flaky_parse)

    bad = str(kb_env / "bad.pdf")
    good = str(kb_env / "good.pdf")
    results = asyncio.run(
        auto_pipeline.run_auto_pipeline(KB, [bad, good], base_dir=kb_env, **stubs)
    )

    # The broken file stops at parse; the other one runs to the end.
    assert results["bad.pdf"]["status"] == "error"
    assert results["bad.pdf"]["stage"] == "parsed"
    assert results["good.pdf"] == {"status": "ok"}
    assert len(calls["canonicalize"]) == 1

    state = auto_pipeline.read_pipeline_state(KB, base_dir=kb_env)
    assert state["bad.pdf"]["status"] == "error"
    assert state["bad.pdf"]["parsed_error"] == "engine not ready"
    assert "parsed" not in state["bad.pdf"]
    assert state["good.pdf"]["status"] == "ok"
    assert calls["import"] == ["book-1"]


def test_chain_canonicalize_failure_marks_error_without_import(kb_env, tmp_path, monkeypatch) -> None:
    calls, holder, stubs = _patch_chain(monkeypatch, books=[])  # canonicalize explodes
    holder["dir"] = _seed_parse_dir(tmp_path)
    pdf = str(kb_env / "书A.pdf")

    results = asyncio.run(
        auto_pipeline.run_auto_pipeline(KB, [pdf], base_dir=kb_env, **stubs)
    )

    assert results["书A.pdf"]["status"] == "error"
    assert results["书A.pdf"]["stage"] == "canonicalized"
    assert calls["import"] == []  # import never runs after a canonicalize break

    record = auto_pipeline.read_pipeline_state(KB, base_dir=kb_env)["书A.pdf"]
    assert record["status"] == "error"
    assert "canonicalize exploded" in record["canonicalized_error"]
    # The stages that did succeed stay on record.
    assert record["parsed"]["workdir"] == holder["dir"]
    assert "imported" not in record


def test_switch_off_by_default(tmp_path) -> None:
    """Regression: no flag in the manifest → the chain must stay off."""
    base_dir = tmp_path / "kbs"
    base_dir.mkdir()
    _register_kb(base_dir)  # no metadata at all
    assert auto_pipeline.auto_pipeline_enabled("教材-测试", base_dir=base_dir) is False

    _register_kb(base_dir, metadata={"auto_pipeline": False})
    assert auto_pipeline.auto_pipeline_enabled("教材-测试", base_dir=base_dir) is False

    _register_kb(base_dir, metadata={"auto_pipeline": "false"})
    assert auto_pipeline.auto_pipeline_enabled("教材-测试", base_dir=base_dir) is False

    _register_kb(base_dir, metadata={"auto_pipeline": True})
    assert auto_pipeline.auto_pipeline_enabled("教材-测试", base_dir=base_dir) is True


# ─────────────────────────────────────────────────────────────────────────────
# P7【1】canonicalize 内联树生成
# ─────────────────────────────────────────────────────────────────────────────


def test_layout_para_blocks_flatten_to_content_list() -> None:
    layout = {
        "pdf_info": [
            {
                "page_idx": 0,
                "para_blocks": [
                    {"type": "image", "blocks": []},
                    {
                        "type": "title",
                        "level": 2,
                        "lines": [{"spans": [{"content": "第一课 原始社会"}]}],
                    },
                    {
                        "type": "text",
                        "lines": [{"spans": [{"content": "正文第一段。"}]}],
                    },
                ],
            }
        ]
    }
    blocks = canonical_tree._content_list_from_layout(layout)
    assert blocks == [
        {"type": "title", "text": "第一课 原始社会", "text_level": 2},
        {"type": "text", "text": "正文第一段。"},
    ]


def test_inline_rebuild_caches_tree_when_p0_misses(tmp_path, monkeypatch) -> None:
    service = PathService(workspace_root=tmp_path / "data")
    monkeypatch.setattr(storage_module, "get_path_service", lambda: service)
    storage_module._storages.clear()
    storage = storage_module.get_book_storage()
    from deeptutor.book.models import Book, BookStatus

    book = Book(title="大部头", status=BookStatus.DRAFT)
    storage.save_book(book)
    monkeypatch.setattr(enrich_module, "enrich", lambda *a, **k: {"tree": LAYOUT_TREE})

    layout = {
        "pdf_info": [
            {
                "page_idx": 0,
                "para_blocks": [
                    {
                        "type": "title",
                        "level": 1,
                        "lines": [{"spans": [{"content": "第一单元"}]}],
                    }
                ],
            }
        ]
    }
    assert (
        canonical_tree.rebuild_canonical_tree_from_layout(
            book.id, layout, filename="大部头.pdf", storage=storage
        )
        is True
    )
    tree = storage.load_canonical_kp_tree(book.id)
    assert tree is not None
    assert tree["children"][0]["children"][0]["type"] == "mu"
    assert tree["children"][0]["children"][0]["node_id"] == "m1m1m1m1m1m1"


def test_inline_rebuild_is_best_effort(tmp_path, monkeypatch) -> None:
    service = PathService(workspace_root=tmp_path / "data")
    monkeypatch.setattr(storage_module, "get_path_service", lambda: service)
    storage_module._storages.clear()
    storage = storage_module.get_book_storage()
    from deeptutor.book.models import Book, BookStatus

    book = Book(title="大部头", status=BookStatus.DRAFT)
    storage.save_book(book)

    def boom(*args, **kwargs):
        raise RuntimeError("enrich exploded")

    monkeypatch.setattr(enrich_module, "enrich", boom)
    assert (
        canonical_tree.rebuild_canonical_tree_from_layout(
            book.id, {"pdf_info": []}, storage=storage
        )
        is False
    )
    assert storage.load_canonical_kp_tree(book.id) is None

    # An enrich tree that fails the full-shape gate is not cached either.
    monkeypatch.setattr(
        enrich_module,
        "enrich",
        lambda *a, **k: {"tree": {"title": "书", "children": [{"title": "目录"}]}},
    )
    assert (
        canonical_tree.rebuild_canonical_tree_from_layout(
            book.id, {"pdf_info": []}, storage=storage
        )
        is False
    )
    assert storage.load_canonical_kp_tree(book.id) is None


def test_canonicalize_endpoint_falls_back_to_inline_rebuild(tmp_path, monkeypatch) -> None:
    """P0 miss (no usable doc_tree) + a layout in the request → the tree in the
    book manifest comes from the inline rebuild, no external patch script."""
    from tests.api.test_book_canonicalize import _TOC, _new_client

    client, storage, _engine = _new_client(tmp_path, monkeypatch)
    monkeypatch.setattr(canonical_tree, "load_kb_doc_trees", lambda _kbs: [])
    monkeypatch.setattr(enrich_module, "enrich", lambda *a, **k: {"tree": LAYOUT_TREE})

    layout = {
        "pdf_info": [
            {
                "page_idx": 0,
                "para_blocks": [
                    {
                        "type": "title",
                        "level": 1,
                        "lines": [{"spans": [{"content": "第一课 原始社会的解体"}]}],
                    }
                ],
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
        json={
            "title": "政治必修1",
            "source": "layout_json",
            "layout": layout,
            "language": "zh",
            "knowledge_bases": ["数学KB"],
            "chapters": [],
        },
    )
    assert response.status_code == 200
    book_id = response.json()["book"]["id"]
    tree = storage.load_canonical_kp_tree(book_id)
    assert tree is not None and tree["children"][0]["children"][0]["type"] == "mu"

    # And when the KB docstore had a usable doc_tree, the inline path is skipped:
    monkeypatch.setattr(canonical_tree, "load_kb_doc_trees", lambda _kbs: [LAYOUT_TREE])
    rebuild_calls: list[Any] = []

    def spy_rebuild(*args, **kwargs):
        rebuild_calls.append(args)
        return True

    monkeypatch.setattr(canonical_tree, "rebuild_canonical_tree_from_layout", spy_rebuild)
    response = client.post(
        "/api/v1/book/books/canonicalize",
        json={"title": "另一本", "toc": _TOC, "language": "zh", "chapters": []},
    )
    assert response.status_code == 200
    assert rebuild_calls == []
