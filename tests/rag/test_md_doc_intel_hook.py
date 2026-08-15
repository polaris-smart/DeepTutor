"""Tests for the md text-path doc_intel hook (parse-cache content_list backfill).

md files are direct-read via the ``text_files`` branch, which never sets
``_last_parsed_blocks`` — so doc_intel enrich (classification / doc_tree) was
always skipped for them. The hook re-looks-up the parse cache by source hash
and feeds the cached content_list blocks into enrich. Fail-open by contract: a
miss or a broken cache entry indexes plain text with clean metadata and never
raises.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from deeptutor.services.parsing import cache as parse_cache
from deeptutor.services.rag.pipelines.llamaindex.document_loader import (
    LlamaIndexDocumentLoader,
)

# MinerU v1 content_list blocks for a math textbook — unit/lesson/section
# headings with ``text_level``, so enrich builds a doc tree and classifies.
_TEXTBOOK_BLOCKS = [
    {"type": "text", "text": "普通高中教科书", "text_level": 1, "page_idx": 0},
    {"type": "text", "text": "第1章 集合", "text_level": 2, "page_idx": 1},
    {"type": "text", "text": "1.1 集合的概念", "text_level": 3, "page_idx": 1},
    {"type": "text", "text": "集合是数学中最基本的概念之一。", "page_idx": 1},
    {"type": "text", "text": "第2章 常用逻辑用语", "text_level": 2, "page_idx": 3},
    {"type": "text", "text": "2.1 命题、定理、定义", "text_level": 3, "page_idx": 3},
]

_MD_TEXT = """# 普通高中教科书

## 第1章 集合

### 1.1 集合的概念

集合是数学中最基本的概念之一。

## 第2章 常用逻辑用语

### 2.1 命题、定理、定义
"""


def _write_cache_entry(
    cache_root: Path, source_path: Path, blocks: list[dict], signature: str
) -> Path:
    """Write one ready parse-cache entry keyed by ``source_path`` bytes.

    Mirrors the on-disk layout ``parse_cache/<h[:2]>/<source_hash>/<signature>/
    <doc>/<uuid>_content_list.json`` (manifest stamped last → ready).
    """
    source_hash = parse_cache.source_hash_from_path(source_path)
    sig_dir = parse_cache.signature_dir(cache_root, source_hash, signature)
    doc_dir = sig_dir / source_path.stem
    doc_dir.mkdir(parents=True)
    (doc_dir / "full.md").write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    (doc_dir / "00000000-0000-0000-0000-000000000000_content_list.json").write_text(
        json.dumps(blocks, ensure_ascii=False), encoding="utf-8"
    )
    parse_cache.write_manifest(
        sig_dir,
        {
            "engine": "mineru",
            "signature": signature,
            "source_hash": source_hash,
            "source_name": source_path.name,
        },
    )
    return sig_dir


class _StubPathService:
    """PathService stand-in whose parse cache lives under a tmp workspace."""

    def __init__(self, workspace_root: Path) -> None:
        self._workspace_root = workspace_root

    def get_parse_cache_root(self) -> Path:
        return self._workspace_root / "parse_cache"


def _install_stub_path_service(
    monkeypatch: pytest.MonkeyPatch, workspace_root: Path
) -> None:
    monkeypatch.setattr(
        "deeptutor.services.path_service.get_path_service",
        lambda: _StubPathService(workspace_root),
    )


def _load(md_path: Path) -> list[object]:
    return asyncio.run(LlamaIndexDocumentLoader().load([str(md_path)]))


def test_md_loads_doc_intel_metadata_from_cached_content_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("llama_index.core")
    md_path = tmp_path / "数学必修一 教师教学用书.md"
    md_path.write_text(_MD_TEXT, encoding="utf-8")

    _install_stub_path_service(monkeypatch, tmp_path)
    cache_root = tmp_path / "parse_cache"
    # A ready signature dir with no content_list comes first; the hook must
    # probe every signature dir under the source hash and use the first hit.
    _write_cache_entry(cache_root, md_path, [], signature="aaaaaaaaaaaaaaaa")
    _write_cache_entry(cache_root, md_path, _TEXTBOOK_BLOCKS, signature="9d6644571f0168e2")

    documents = _load(md_path)

    assert len(documents) == 1
    metadata = documents[0].metadata
    assert metadata["file_name"] == "数学必修一 教师教学用书.md"
    assert metadata["doc_subject"] == "数学"
    assert metadata["doc_type"] == "textbook"
    assert metadata["doc_grade"] == "高一"
    tree = json.loads(metadata["doc_tree"])
    assert [node["title"] for node in tree["children"]] == [
        "第1章 集合",
        "第2章 常用逻辑用语",
    ]
    assert documents[0].text.strip() == _MD_TEXT.strip()


def test_md_without_cache_entry_indexes_clean_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("llama_index.core")
    md_path = tmp_path / "plain notes.md"
    md_path.write_text("# Title\n\nSome text.", encoding="utf-8")
    # No parse_cache directory at all — get_parse_cache_root() points nowhere.
    _install_stub_path_service(monkeypatch, tmp_path)

    documents = _load(md_path)

    assert len(documents) == 1
    metadata = documents[0].metadata
    assert metadata["file_name"] == "plain notes.md"
    assert "doc_subject" not in metadata
    assert "doc_type" not in metadata
    assert "doc_grade" not in metadata
    assert "doc_tree" not in metadata
    assert documents[0].text == "# Title\n\nSome text."


def test_md_with_corrupt_cache_entry_falls_back_to_plain_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("llama_index.core")
    md_path = tmp_path / "数学必修一 教师教学用书.md"
    md_path.write_text(_MD_TEXT, encoding="utf-8")
    _install_stub_path_service(monkeypatch, tmp_path)

    cache_root = tmp_path / "parse_cache"
    source_hash = parse_cache.source_hash_from_path(md_path)
    sig_dir = parse_cache.signature_dir(cache_root, source_hash, "9d6644571f0168e2")
    doc_dir = sig_dir / md_path.stem
    doc_dir.mkdir(parents=True)
    (doc_dir / "00000000-0000-0000-0000-000000000000_content_list.json").write_text(
        "{ not valid json", encoding="utf-8"
    )
    parse_cache.write_manifest(
        sig_dir,
        {
            "engine": "mineru",
            "signature": "9d6644571f0168e2",
            "source_hash": source_hash,
            "source_name": md_path.name,
        },
    )

    documents = _load(md_path)

    assert len(documents) == 1
    assert "doc_subject" not in documents[0].metadata
    assert "doc_tree" not in documents[0].metadata


def test_md_cache_miss_does_not_inherit_previous_parser_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("llama_index.core")
    from deeptutor.services.parsing.types import ParsedDocument

    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"stub")
    md_path = tmp_path / "plain notes.md"
    md_path.write_text("# Title\n\nSome text.", encoding="utf-8")

    class _StubService:
        def parse(self, source_path, **_kwargs):
            return ParsedDocument(
                markdown="Paper body",
                blocks=[{"type": "text", "text": "数学", "text_level": 1}],
            )

    monkeypatch.setattr("deeptutor.services.parsing.get_parse_service", lambda: _StubService())
    _install_stub_path_service(monkeypatch, tmp_path)  # no cache entry for the md

    documents = asyncio.run(
        LlamaIndexDocumentLoader().load([str(pdf_path), str(md_path)])
    )

    by_name = {doc.metadata["file_name"]: doc for doc in documents}
    # The PDF enriched with its own parsed blocks...
    assert by_name["paper.pdf"].metadata["doc_subject"] == "数学"
    # ...but the md (no cache hit) must not inherit those blocks.
    assert "doc_subject" not in by_name["plain notes.md"].metadata
    assert "doc_tree" not in by_name["plain notes.md"].metadata
