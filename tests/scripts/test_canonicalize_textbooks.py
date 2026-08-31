"""Unit tests for the canonicalization orchestrator's pure logic.

Network is faked — these cover the parts that decide what gets imported and
what counts as a success, which is where a silent mistake would ship a
mutilated textbook.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest


def _load_module():
    """``scripts/`` is not a package — load the file the way its siblings do."""
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "canonicalize_textbooks.py"
    spec = importlib.util.spec_from_file_location("canonicalize_textbooks_under_test", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load_module()
OrchestratorError = _mod.OrchestratorError
collect_inputs = _mod.collect_inputs
fetch_chapter_pages = _mod.fetch_chapter_pages
preflight_parse_engine = _mod.preflight_parse_engine
process_book = _mod.process_book
read_textbook_tree = _mod.read_textbook_tree
tree_to_toc = _mod.tree_to_toc


class _FakeApi:
    """Records calls and replays canned JSON keyed by (method, url)."""

    def __init__(self, responses: dict[tuple[str, str], Any] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, str, dict]] = []

    def get(self, url: str, **kwargs: Any) -> Any:
        self.calls.append(("GET", url, kwargs))
        return self._resolve("GET", url, kwargs)

    def post(self, url: str, **kwargs: Any) -> Any:
        self.calls.append(("POST", url, kwargs))
        return self._resolve("POST", url, kwargs)

    def _resolve(self, method: str, url: str, kwargs: dict) -> Any:
        value = self.responses.get((method, url))
        return value(kwargs) if callable(value) else (value or {})


# ── tree_to_toc ──────────────────────────────────────────────────────────────


def test_tree_to_toc_preserves_nesting_and_order() -> None:
    tree = {
        "title": "政治必修1",
        "children": [
            {"title": "第一课", "children": [{"title": "第一框"}, {"title": "第二框"}]},
            {"title": "第二课"},
        ],
    }
    assert tree_to_toc(tree) == [
        {"title": "第一课", "children": [{"title": "第一框"}, {"title": "第二框"}]},
        {"title": "第二课"},
    ]


def test_tree_to_toc_drops_untitled_nodes() -> None:
    tree = {"children": [{"title": "  "}, {"title": "第一课"}, {"nope": 1}]}
    assert tree_to_toc(tree) == [{"title": "第一课"}]


def test_tree_to_toc_of_an_empty_tree_is_empty() -> None:
    assert tree_to_toc({}) == []
    assert tree_to_toc({"children": []}) == []


# ── read_textbook_tree ───────────────────────────────────────────────────────


def test_read_textbook_tree_matches_on_file_name() -> None:
    api = _FakeApi(
        {
            ("GET", "/api/v1/knowledge/数学/textbook-tree"): {
                "textbooks": [
                    {"file_name": "别的书.pdf", "tree": {"title": "别的"}},
                    {"file_name": "数学必修1.pdf", "tree": {"title": "数学"}, "subject": "数学"},
                ]
            }
        }
    )
    entry = read_textbook_tree(api, "数学", "数学必修1.pdf")
    assert entry["subject"] == "数学"


def test_read_textbook_tree_raises_when_the_parse_produced_no_structure() -> None:
    api = _FakeApi({("GET", "/api/v1/knowledge/数学/textbook-tree"): {"textbooks": []}})
    with pytest.raises(OrchestratorError, match="no doc_intel tree"):
        read_textbook_tree(api, "数学", "数学必修1.pdf")


# ── fetch_chapter_pages ──────────────────────────────────────────────────────


def _by_struct(nodes_by_path: dict[str, list[dict]]):
    def handler(kwargs: dict) -> dict:
        path = kwargs["params"]["path"]
        return {"nodes": nodes_by_path.get(path, [])}

    return handler


def test_fetch_chapter_pages_requests_full_text_not_previews() -> None:
    api = _FakeApi(
        {
            ("GET", "/api/v1/knowledge/数学/docs/by-struct"): _by_struct(
                {"第一章": [{"struct_path": "第一章/1.1", "text": "集合的含义"}]}
            )
        }
    )
    fetch_chapter_pages(api, "数学", [{"title": "第一章"}], limit_per_chapter=50)
    params = api.calls[0][2]["params"]
    # Previews are truncated at 200 chars — importing them would ship a
    # mutilated textbook, so full_text is mandatory here.
    assert params["full_text"] == "true"
    assert params["limit"] == 50


def test_fetch_chapter_pages_builds_verbatim_reading_blocks() -> None:
    api = _FakeApi(
        {
            ("GET", "/api/v1/knowledge/数学/docs/by-struct"): _by_struct(
                {
                    "第一章": [
                        {"struct_path": "第一章/1.1", "text": "集合的含义"},
                        {"struct_path": "第一章/1.2", "text": "集合间的关系"},
                    ]
                }
            )
        }
    )
    groups = fetch_chapter_pages(api, "数学", [{"title": "第一章"}], limit_per_chapter=100)
    assert len(groups) == 1
    blocks = groups[0]["pages"][0]["blocks"]
    assert [b["block_type"] for b in blocks] == ["reading", "reading"]
    assert [b["params"]["body"] for b in blocks] == ["集合的含义", "集合间的关系"]
    assert blocks[0]["params"]["source_label"] == "第一章/1.1"


def test_fetch_chapter_pages_keeps_chapter_index_aligned_with_the_toc() -> None:
    api = _FakeApi(
        {
            ("GET", "/api/v1/knowledge/数学/docs/by-struct"): _by_struct(
                {"第三章": [{"struct_path": "第三章", "text": "函数"}]}
            )
        }
    )
    toc = [{"title": "第一章"}, {"title": "第二章"}, {"title": "第三章"}]
    groups = fetch_chapter_pages(api, "数学", toc, limit_per_chapter=100)
    # Chapters without prose are skipped, but the surviving group must still
    # point at its own index in the spine — off-by-one here files content
    # under the wrong chapter.
    assert [g["chapter_index"] for g in groups] == [2]


def test_fetch_chapter_pages_skips_blank_nodes() -> None:
    api = _FakeApi(
        {
            ("GET", "/api/v1/knowledge/数学/docs/by-struct"): _by_struct(
                {"第一章": [{"text": "   "}, {"text": ""}]}
            )
        }
    )
    assert fetch_chapter_pages(api, "数学", [{"title": "第一章"}], limit_per_chapter=100) == []


# ── process_book: what counts as success ─────────────────────────────────────


def _process_api(*, pages_created: int) -> _FakeApi:
    return _FakeApi(
        {
            ("GET", "/api/v1/knowledge/数学/textbook-tree"): {
                "textbooks": [
                    {
                        "file_name": "数学必修1.pdf",
                        "subject": "数学",
                        "tree": {"children": [{"title": "第一章"}]},
                    }
                ]
            },
            ("GET", "/api/v1/knowledge/数学/docs/by-struct"): _by_struct(
                {"第一章": [{"struct_path": "第一章", "text": "集合"}]}
            ),
            ("POST", "/api/v1/book/books/canonicalize"): {
                "book": {"id": "bk_1", "status": "ready"},
                "pages_created": pages_created,
            },
        }
    )


def test_process_book_reports_success_with_counts(tmp_path) -> None:
    path = tmp_path / "数学必修1.pdf"
    path.write_bytes(b"%PDF-1.4\n")
    row = process_book(
        _process_api(pages_created=1),
        "数学",
        path,
        index_timeout=1.0,
        blocks_per_chapter=100,
        dry_run=True,
    )
    assert row["ok"] is True
    assert row["chapters_detected"] == 1
    assert row["chapters_with_content"] == 1
    assert row["subject"] == "数学"


def test_process_book_treats_a_page_less_import_as_failure(tmp_path) -> None:
    path = tmp_path / "数学必修1.pdf"
    path.write_bytes(b"%PDF-1.4\n")
    api = _process_api(pages_created=0)
    # dry_run=False so the canonicalize response is what decides the verdict;
    # upload/index are stubbed out by the fake API returning {}.
    row = process_book(
        api, "数学", path, index_timeout=1.0, blocks_per_chapter=100, dry_run=False
    )
    assert row["ok"] is False
    assert "0 pages" in row["error"]


def test_process_book_records_a_failure_instead_of_raising(tmp_path) -> None:
    path = tmp_path / "扫描件.pdf"
    path.write_bytes(b"%PDF-1.4\n")
    api = _FakeApi({("GET", "/api/v1/knowledge/数学/textbook-tree"): {"textbooks": []}})
    row = process_book(
        api, "数学", path, index_timeout=1.0, blocks_per_chapter=100, dry_run=True
    )
    assert row["ok"] is False
    assert "no doc_intel tree" in row["error"]
    assert "elapsed_seconds" in row


# ── preflight_parse_engine ───────────────────────────────────────────────────


def test_preflight_accepts_a_structure_capable_engine() -> None:
    api = _FakeApi({("GET", "/api/v1/settings/document-parsing"): {"engine": "mineru"}})
    assert preflight_parse_engine(api) == "mineru"


def test_preflight_rejects_a_markdown_only_engine() -> None:
    # text_only / pymupdf4llm / markitdown emit markdown but no content_list,
    # so doc_intel gets no blocks and every book would canonicalize to an
    # empty tree. Fail before uploading rather than after a 40-minute parse.
    api = _FakeApi({("GET", "/api/v1/settings/document-parsing"): {"engine": "text_only"}})
    with pytest.raises(OrchestratorError, match="text_only"):
        preflight_parse_engine(api)


def test_preflight_names_the_fix_in_its_message() -> None:
    api = _FakeApi({("GET", "/api/v1/settings/document-parsing"): {"engine": "markitdown"}})
    with pytest.raises(OrchestratorError, match="MinerU"):
        preflight_parse_engine(api)


# ── collect_inputs ───────────────────────────────────────────────────────────


def test_collect_inputs_expands_directories_and_filters_by_suffix(tmp_path) -> None:
    (tmp_path / "a.pdf").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "cover.png").write_bytes(b"x")
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "b.epub").write_bytes(b"x")

    found = [p.name for p in collect_inputs([str(tmp_path)])]
    assert sorted(found) == ["a.pdf", "b.epub", "notes.txt"]


def test_collect_inputs_accepts_explicit_files(tmp_path) -> None:
    target = tmp_path / "one.pdf"
    target.write_bytes(b"x")
    assert collect_inputs([str(target)]) == [target]
