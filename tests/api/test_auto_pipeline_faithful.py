"""auto_pipeline 保真链贯通（P7-faithful）——一本书一条链的编排契约.

覆盖任务书要求的新测试面：

* 保真链七步编排顺序（canonicalize → figures → latex → import → tree →
  share → qb），每一步都是仓内既有件的串联，测试只盯调用顺序与产物落位。
* ``mode="faithful"`` 0 章 fail-loud（带指引），绝不静默落 AI 生成路径。
* 缺省 ``mode="auto"`` 的 legacy 兜底路径，返回/状态里标注
  ``mode="legacy_toc"``。
* 参数透传：授权用户列表（share read）、题库开关（enable_qb），以及 KB
  manifest ``auto_pipeline`` 用 dict 形态下发的选项。
* 增强段（latex/figures）失败不拦后续段、qb 触发失败整文件置 error。
"""

from __future__ import annotations

import asyncio
import importlib
import json
from types import SimpleNamespace
from typing import Any

import pytest

import deeptutor.book.canonical_tree as canonical_tree
import deeptutor.book.figure_backfill as figure_backfill
import deeptutor.book.latex_delimit as latex_delimit
import deeptutor.knowledge.auto_pipeline as auto_pipeline
import deeptutor.knowledge.doc_intel as _doc_intel
import deeptutor.learning.ingest_pipeline as ingest_pipeline
import deeptutor.multi_user.identity as identity_module

# The doc_intel package re-exports the ``enrich`` function, shadowing the
# submodule attribute — resolve the module explicitly for patching.
enrich_module = importlib.import_module("deeptutor.knowledge.doc_intel.enrich")
_ = _doc_intel

from deeptutor.knowledge.manager import KnowledgeBaseManager

KB = "教材-保真"

#: 带页脚章标的两页 layout —— 保真法能重建出 2 章。
LAYOUT_FAITHFUL = {
    "pdf_info": [
        {
            "page_idx": 0,
            "para_blocks": [
                {"type": "text", "lines": [{"spans": [{"content": "第一章正文。"}]}]},
            ],
            "discarded_blocks": [
                {"type": "footer", "lines": [{"spans": [{"content": "第一章 有理数"}]}]},
                {"type": "page_number", "lines": [{"spans": [{"content": "1"}]}]},
            ],
        },
        {
            "page_idx": 1,
            "para_blocks": [
                {"type": "text", "lines": [{"spans": [{"content": "第二章正文。"}]}]},
            ],
            "discarded_blocks": [
                {"type": "footer", "lines": [{"spans": [{"content": "第二章 整式的加减"}]}]},
                {"type": "page_number", "lines": [{"spans": [{"content": "12"}]}]},
            ],
        },
    ]
}

#: 无页脚章标的 layout —— 保真法 0 章。
LAYOUT_NO_CHAPTER = {
    "pdf_info": [
        {
            "page_idx": 0,
            "para_blocks": [
                {"type": "text", "lines": [{"spans": [{"content": "讲义正文。"}]}]},
            ],
            "discarded_blocks": [],
        }
    ]
}

ENRICH_TREE = {
    "title": "讲义",
    "children": [
        {
            "title": "第一部分",
            "level": 1,
            "children": [{"title": "1.1 概述", "level": 2}],
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
def kb_env(tmp_path) -> Any:
    base_dir = tmp_path / "kbs"
    base_dir.mkdir()
    _register_kb(base_dir, metadata={"auto_pipeline": True})
    return base_dir


def _seed_parse_dir(tmp_path, *, layout: dict[str, Any] | None, content_list: list | None) -> str:
    """A parse product dir under tmp: md + optional layout + content list."""
    product = tmp_path / "parse" / "书B"
    product.mkdir(parents=True)
    (product / "书B.md").write_text("教材原文", encoding="utf-8")
    if layout is not None:
        (product / "layout.json").write_text(json.dumps(layout, ensure_ascii=False), encoding="utf-8")
    if content_list is not None:
        (product / "书B_content_list.json").write_text(
            json.dumps(content_list, ensure_ascii=False), encoding="utf-8"
        )
    return str(product)


def _patch_chain(monkeypatch, *, order: list[str] | None = None) -> dict[str, list[Any]]:
    """Stub every seam the faithful chain composes, recording the call order."""
    calls: dict[str, list[Any]] = {"grants": [], "qb": []}

    def mark(stage: str):
        def _m(*args, **kwargs):
            if order is not None:
                order.append(stage)
            return True

        return _m

    async def fake_canonicalize(request):
        if order is not None:
            order.append("canonicalize")
        calls["canonicalize_request"] = request
        book = {"id": "book-9", "title": request.title, "chapter_count": 2, "page_count": 2}
        return {"book": book}

    async def fake_import_from_book(book_id, body):
        if order is not None:
            order.append("import")
        calls["import"] = book_id
        return {"status": "ok", "module_count": 3}

    class _FakePage:
        id = "pg_1"
        title = "页1"

    class _FakeEngine:
        def __init__(self, *, storage=None, compiler_options=None):
            self.storage = storage

        def list_pages(self, book_id):
            return [_FakePage()]

        async def insert_block(self, **kwargs):
            return object()

    async def fake_apply_plan(engine, book_id, plan, *, content_dir, storage=None):
        if order is not None:
            order.append("figures.apply")
        calls["figures"] = {"pages": 1, "blocks_inserted": 1, "images_copied": 1}
        return dict(calls["figures"])

    def fake_fix_book(book_id, *, dry_run=False, storage=None):
        if order is not None:
            order.append("latex")
        return {"book_id": book_id, "blocks_fixed": 2, "pages_touched": 1}

    def fake_start_pipeline(kb_name, book_id, *, deps=None, background=True):
        if order is not None:
            order.append("qb")
        calls["qb"].append((kb_name, book_id))
        return {"run_id": "run_qb1", "status": "queued"}

    def fake_set_book_grant(username, book_id, level):
        calls["grants"].append((username, book_id, level))
        return username != "幽灵"

    monkeypatch.setattr(
        "deeptutor.services.parsing.service.ParseService.parse",
        lambda self, source_path, **kwargs: SimpleNamespace(
            workdir=calls["workdir"], engine="text_only"
        ),
    )
    monkeypatch.setattr("deeptutor.book.engine.BookEngine", _FakeEngine)
    monkeypatch.setattr(
        figure_backfill,
        "load_content_lists",
        lambda parse_dir: ([{"type": "image", "img_path": "images/a.jpg", "page_idx": 0}], parse_dir),
    )

    def fake_plan(book_id, pages, parse_dir, **kwargs):
        if order is not None:
            order.append("figures.plan")
        calls["figure_plan"] = book_id
        return SimpleNamespace(entries=[SimpleNamespace(images=[1])])

    monkeypatch.setattr(figure_backfill, "plan_from_parse_dir", fake_plan)
    monkeypatch.setattr(figure_backfill, "apply_plan", fake_apply_plan)
    monkeypatch.setattr(latex_delimit, "fix_book", fake_fix_book)
    monkeypatch.setattr(
        canonical_tree, "cache_canonical_tree_for_book", mark("tree.docstore")
    )
    monkeypatch.setattr(
        canonical_tree, "rebuild_canonical_tree_from_layout", mark("tree.layout")
    )
    monkeypatch.setattr(ingest_pipeline, "start_pipeline", fake_start_pipeline)
    monkeypatch.setattr(identity_module, "set_book_grant", fake_set_book_grant)
    monkeypatch.setattr(enrich_module, "enrich", lambda *a, **k: {"tree": ENRICH_TREE})
    calls["stubs"] = {"canonicalize": fake_canonicalize, "import": fake_import_from_book}
    return calls


def _chain_kwargs(calls: dict[str, Any]) -> dict[str, Any]:
    return {
        "canonicalize": (calls["stubs"]["canonicalize"], SimpleNamespace),
        "import_from_book": calls["stubs"]["import"],
        "import_request_model": SimpleNamespace,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 保真链编排顺序 + 返回契约
# ─────────────────────────────────────────────────────────────────────────────


def test_faithful_chain_runs_all_stages_in_order(kb_env, tmp_path, monkeypatch) -> None:
    order: list[str] = []
    calls = _patch_chain(monkeypatch, order=order)
    calls["workdir"] = _seed_parse_dir(
        tmp_path, layout=LAYOUT_FAITHFUL, content_list=[{"type": "image", "img_path": "images/a.jpg", "page_idx": 0}]
    )
    pdf = str(kb_env / "书B.pdf")

    results = asyncio.run(
        auto_pipeline.run_auto_pipeline(KB, [pdf], base_dir=kb_env, **_chain_kwargs(calls))
    )

    # 编排顺序：建书 → 插图(plan/apply) → 公式 → import → 树落缓 → 题库。
    # share 缺省不共享，无授权调用。
    assert order == [
        "canonicalize",
        "figures.plan",
        "figures.apply",
        "latex",
        "import",
        "tree.docstore",
        "qb",
    ]
    assert calls["qb"] == [(KB, "book-9")]
    assert calls["grants"] == []

    result = results["书B.pdf"]
    assert result["status"] == "ok"
    assert result["book_id"] == "book-9"
    assert result["mode"] == "faithful"
    assert result["chapters"] == 2
    assert result["pages"] == 2
    assert result["figures"] == {"pages": 1, "blocks_inserted": 1, "images_copied": 1}
    assert result["tree_cached"] is True
    assert result["qb_pipeline_run_id"] == "run_qb1"

    record = auto_pipeline.read_pipeline_state(KB, base_dir=kb_env)["书B.pdf"]
    assert record["status"] == "ok"
    assert record["canonicalized"]["mode"] == "faithful"
    assert record["figures"]["blocks_inserted"] == 1
    assert record["latex"]["blocks_fixed"] == 2
    assert record["imported"]["tree_cached"] is True
    assert record["qb"]["run_id"] == "run_qb1"


def test_faithful_mode_zero_chapters_fails_loud(kb_env, tmp_path, monkeypatch) -> None:
    order: list[str] = []
    calls = _patch_chain(monkeypatch, order=order)
    calls["workdir"] = _seed_parse_dir(tmp_path, layout=LAYOUT_NO_CHAPTER, content_list=None)
    pdf = str(kb_env / "书B.pdf")

    results = asyncio.run(
        auto_pipeline.run_auto_pipeline(
            KB, [pdf], base_dir=kb_env, mode="faithful", **_chain_kwargs(calls)
        )
    )

    # fail-loud: canonicalize 未被调用，错误信息带指引。
    assert results["书B.pdf"]["status"] == "error"
    assert results["书B.pdf"]["stage"] == "canonicalized"
    assert "无法识别章节结构" in results["书B.pdf"]["error"]
    assert "mode='auto'" in results["书B.pdf"]["error"]
    assert order == []
    assert "canonicalize_request" not in calls

    record = auto_pipeline.read_pipeline_state(KB, base_dir=kb_env)["书B.pdf"]
    assert record["status"] == "error"
    assert "canonicalized_error" in record


def test_auto_mode_zero_chapters_falls_back_to_legacy_toc(kb_env, tmp_path, monkeypatch) -> None:
    order: list[str] = []
    calls = _patch_chain(monkeypatch, order=order)
    calls["workdir"] = _seed_parse_dir(
        tmp_path, layout=LAYOUT_NO_CHAPTER, content_list=[{"type": "text", "text": "讲义正文"}]
    )
    pdf = str(kb_env / "书B.pdf")

    results = asyncio.run(
        auto_pipeline.run_auto_pipeline(KB, [pdf], base_dir=kb_env, **_chain_kwargs(calls))
    )

    # 兜底走通且明确标注 legacy_toc。
    assert results["书B.pdf"]["status"] == "ok"
    assert results["书B.pdf"]["mode"] == "legacy_toc"
    request = calls["canonicalize_request"]
    assert request.source == "toc_json"
    assert request.toc and request.toc[0]["title"] == "第一部分"
    record = auto_pipeline.read_pipeline_state(KB, base_dir=kb_env)["书B.pdf"]
    assert record["canonicalized"]["mode"] == "legacy_toc"


# ─────────────────────────────────────────────────────────────────────────────
# 参数透传：授权 / 题库开关
# ─────────────────────────────────────────────────────────────────────────────


def test_share_read_users_and_qb_disabled(kb_env, tmp_path, monkeypatch) -> None:
    order: list[str] = []
    calls = _patch_chain(monkeypatch, order=order)
    calls["workdir"] = _seed_parse_dir(tmp_path, layout=LAYOUT_FAITHFUL, content_list=None)
    pdf = str(kb_env / "书B.pdf")

    results = asyncio.run(
        auto_pipeline.run_auto_pipeline(
            KB,
            [pdf],
            base_dir=kb_env,
            enable_qb=False,
            share_read_users=["王老师", "幽灵", ""],
            **_chain_kwargs(calls),
        )
    )

    result = results["书B.pdf"]
    assert result["status"] == "ok"
    assert result["qb_pipeline_run_id"] == ""  # 题库关了
    assert calls["qb"] == []
    # 未知用户 fail-soft 记 skipped，不中止链（grants 记录含失败尝试）。
    assert calls["grants"] == [("王老师", "book-9", "read"), ("幽灵", "book-9", "read")]
    record = auto_pipeline.read_pipeline_state(KB, base_dir=kb_env)["书B.pdf"]
    assert record["imported"]["shared_read"] == ["王老师"]
    assert record["imported"]["share_skipped"] == ["幽灵"]
    assert "qb" not in record


def test_chain_options_from_manifest_dict(kb_env, tmp_path, monkeypatch) -> None:
    _register_kb(
        kb_env,
        metadata={
            "auto_pipeline": {
                "mode": "faithful",
                "enable_qb": False,
                "share_read_users": ["李老师"],
            }
        },
    )
    assert auto_pipeline.auto_pipeline_enabled(KB, base_dir=kb_env) is True
    options = auto_pipeline.read_chain_options(KB, base_dir=kb_env)
    assert options == {"mode": "faithful", "enable_qb": False, "share_read_users": ["李老师"]}

    order: list[str] = []
    calls = _patch_chain(monkeypatch, order=order)
    calls["workdir"] = _seed_parse_dir(tmp_path, layout=LAYOUT_FAITHFUL, content_list=None)
    pdf = str(kb_env / "书B.pdf")

    results = asyncio.run(
        auto_pipeline.run_auto_pipeline(KB, [pdf], base_dir=kb_env, **_chain_kwargs(calls))
    )

    # manifest dict 选项透传：faithful 生效、题库关、授权落王…李老师。
    assert results["书B.pdf"]["mode"] == "faithful"
    assert results["书B.pdf"]["qb_pipeline_run_id"] == ""
    assert calls["qb"] == []
    assert calls["grants"] == [("李老师", "book-9", "read")]


def test_read_chain_options_defaults_and_garbage(tmp_path) -> None:
    base_dir = tmp_path / "kbs"
    base_dir.mkdir()
    _register_kb(base_dir, metadata={"auto_pipeline": True})
    assert auto_pipeline.read_chain_options(KB, base_dir=base_dir) == {
        "mode": "auto",
        "enable_qb": True,
        "share_read_users": [],
    }
    _register_kb(base_dir, metadata={"auto_pipeline": {"mode": "bogus"}})
    assert auto_pipeline.read_chain_options(KB, base_dir=base_dir)["mode"] == "auto"
    _register_kb(base_dir, metadata={"auto_pipeline": {}})  # 空 dict = 关
    assert auto_pipeline.auto_pipeline_enabled(KB, base_dir=base_dir) is False


# ─────────────────────────────────────────────────────────────────────────────
# 增强段失败隔离
# ─────────────────────────────────────────────────────────────────────────────


def test_latex_failure_does_not_block_chain(kb_env, tmp_path, monkeypatch) -> None:
    order: list[str] = []
    calls = _patch_chain(monkeypatch, order=order)
    calls["workdir"] = _seed_parse_dir(tmp_path, layout=LAYOUT_FAITHFUL, content_list=None)
    monkeypatch.setattr(
        latex_delimit, "fix_book", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("delimit boom"))
    )
    pdf = str(kb_env / "书B.pdf")

    results = asyncio.run(
        auto_pipeline.run_auto_pipeline(KB, [pdf], base_dir=kb_env, **_chain_kwargs(calls))
    )

    # 增强段失败记入段位但不拦 import/树/题库。
    assert results["书B.pdf"]["status"] == "ok"
    assert results["书B.pdf"]["qb_pipeline_run_id"] == "run_qb1"
    assert order[-3:] == ["import", "tree.docstore", "qb"]
    record = auto_pipeline.read_pipeline_state(KB, base_dir=kb_env)["书B.pdf"]
    assert record["latex"]["error"] == "delimit boom"
    assert record["imported"]["tree_cached"] is True


def test_qb_start_failure_marks_file_error(kb_env, tmp_path, monkeypatch) -> None:
    calls = _patch_chain(monkeypatch)
    calls["workdir"] = _seed_parse_dir(tmp_path, layout=LAYOUT_FAITHFUL, content_list=None)
    monkeypatch.setattr(
        ingest_pipeline,
        "start_pipeline",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("qb start boom")),
    )
    pdf = str(kb_env / "书B.pdf")

    results = asyncio.run(
        auto_pipeline.run_auto_pipeline(KB, [pdf], base_dir=kb_env, **_chain_kwargs(calls))
    )

    # qb 是核心段：触发失败整文件置 error，其余成功段仍在档。
    assert results["书B.pdf"]["status"] == "error"
    assert results["书B.pdf"]["stage"] == "qb"
    assert "qb start boom" in results["书B.pdf"]["error"]
    record = auto_pipeline.read_pipeline_state(KB, base_dir=kb_env)["书B.pdf"]
    assert record["qb_error"] == "qb start boom"
    assert record["imported"]["tree_cached"] is True
