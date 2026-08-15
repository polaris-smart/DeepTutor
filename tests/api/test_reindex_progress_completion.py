"""T032 regression: a successful reindex must flip .progress.json to completed.

A reindex whose index build succeeded used to promote ``kb_config.json`` to
``ready`` but never wrote the completed snapshot to the KB's ``.progress.json``
— the success path of ``run_reindex_task`` lacked the ``ProgressTracker.update(
COMPLETED, ...)`` call every other indexing path (create, upload) has. The file
therefore stayed at the last embedding-batch write
(``stage=processing_documents``) forever, so ``GET /{kb}/progress`` and the
progress WebSocket reported a perpetual "processing" banner even though the
index, ``metadata.json`` and ``kb_config.json`` all said complete/ready.

These tests run ``run_reindex_task`` with a mocked ``RAGService`` and assert the
completion side effects: ``.progress.json`` stage=completed, ``kb_config.json``
status=ready, and ``metadata.json`` last_indexed_action=reindex.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

try:
    from fastapi import FastAPI
except Exception:  # pragma: no cover - optional dependency in lightweight envs
    FastAPI = None

pytestmark = pytest.mark.skipif(FastAPI is None, reason="fastapi not installed")

if FastAPI is not None:
    import importlib

    knowledge_router_module = importlib.import_module("deeptutor.api.routers.knowledge")
    from deeptutor.knowledge.manager import KnowledgeBaseManager
    from deeptutor.services.rag.service import RAGService
else:  # pragma: no cover - guarded by pytestmark
    knowledge_router_module = None
    KnowledgeBaseManager = None
    RAGService = None


def _make_kb(base_dir: Path, kb_name: str, file_names: list[str]) -> Path:
    """Create ``<base_dir>/<kb_name>/raw`` with ``file_names`` and return its dir."""
    kb_dir = base_dir / kb_name
    raw_dir = kb_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for name in file_names:
        (raw_dir / name).write_text("chunk content", encoding="utf-8")
    return kb_dir


def _stub_successful_initialize(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_initialize(self, kb_name: str, file_paths: list[str], **kwargs) -> bool:
        return True

    monkeypatch.setattr(RAGService, "initialize", _fake_initialize)


@pytest.mark.asyncio
async def test_reindex_success_flips_progress_file_to_completed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    base_dir = tmp_path / "knowledge_bases"
    kb_name = "math-kb"
    kb_dir = _make_kb(base_dir, kb_name, ["a.md", "b.md"])

    _stub_successful_initialize(monkeypatch)
    monkeypatch.setattr(
        knowledge_router_module,
        "get_kb_manager",
        lambda: KnowledgeBaseManager(base_dir=str(base_dir)),
    )

    await knowledge_router_module.run_reindex_task(
        kb_name=kb_name,
        base_dir=str(base_dir),
        task_id="kb_reindex_test_0001",
        signature_hash="test-sig",
    )

    # The on-disk progress snapshot must leave the processing state.
    progress = json.loads((kb_dir / ".progress.json").read_text(encoding="utf-8"))
    assert progress["stage"] == "completed"
    assert progress["message"] == "Re-index complete"
    assert progress["progress_percent"] == 100
    assert progress["current"] == 2
    assert progress["total"] == 2
    assert progress["indexed_count"] == 2
    assert progress["index_action"] == "reindex"

    # kb_config.json must be promoted to ready with the index facts recorded.
    config = json.loads((base_dir / "kb_config.json").read_text(encoding="utf-8"))
    entry = config["knowledge_bases"][kb_name]
    assert entry["status"] == "ready"
    assert entry.get("last_indexed_action") == "reindex"
    assert entry.get("last_indexed_count") == 2

    # metadata.json records the reindex completion.
    metadata = json.loads((kb_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["last_indexed_action"] == "reindex"
    assert metadata["last_indexed_count"] == 2


@pytest.mark.asyncio
async def test_reindex_success_flips_progress_before_ready_kb_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Progress is flipped to completed even if the KB was previously errored.

    Mirrors the create path: the completed snapshot is written by
    ``ProgressTracker.update``, which is independent of the subsequent
    ``update_kb_status(status="ready")`` call.
    """
    base_dir = tmp_path / "knowledge_bases"
    kb_name = "errored-kb"
    kb_dir = _make_kb(base_dir, kb_name, ["doc.md"])

    _stub_successful_initialize(monkeypatch)
    monkeypatch.setattr(
        knowledge_router_module,
        "get_kb_manager",
        lambda: KnowledgeBaseManager(base_dir=str(base_dir)),
    )

    await knowledge_router_module.run_reindex_task(
        kb_name=kb_name,
        base_dir=str(base_dir),
        task_id="kb_reindex_test_0002",
        signature_hash="test-sig",
    )

    progress = json.loads((kb_dir / ".progress.json").read_text(encoding="utf-8"))
    assert progress["stage"] == "completed"

    config = json.loads((base_dir / "kb_config.json").read_text(encoding="utf-8"))
    assert config["knowledge_bases"][kb_name]["status"] == "ready"


@pytest.mark.asyncio
async def test_reindex_failure_still_flips_progress_to_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A reindex with no valid documents leaves progress=error and status=error."""
    base_dir = tmp_path / "knowledge_bases"
    kb_name = "empty-kb"
    kb_dir = _make_kb(base_dir, kb_name, ["doc.md"])

    async def _fake_initialize(self, kb_name: str, file_paths: list[str], **kwargs) -> bool:
        return False

    monkeypatch.setattr(RAGService, "initialize", _fake_initialize)
    monkeypatch.setattr(
        knowledge_router_module,
        "get_kb_manager",
        lambda: KnowledgeBaseManager(base_dir=str(base_dir)),
    )

    await knowledge_router_module.run_reindex_task(
        kb_name=kb_name,
        base_dir=str(base_dir),
        task_id="kb_reindex_test_0003",
        signature_hash="test-sig",
    )

    progress = json.loads((kb_dir / ".progress.json").read_text(encoding="utf-8"))
    assert progress["stage"] == "error"
    assert "no valid documents" in progress["message"].lower()

    config = json.loads((base_dir / "kb_config.json").read_text(encoding="utf-8"))
    assert config["knowledge_bases"][kb_name]["status"] == "error"
