"""Regression tests for bounded LlamaIndex indexing (upstream issue #946)."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest


def _modules() -> tuple[Any, Any, Any]:
    from deeptutor.services.rag.pipelines.llamaindex import pipeline as pipeline_module
    from deeptutor.services.rag.pipelines.llamaindex import storage as storage_module
    from deeptutor.services.rag.pipelines.llamaindex.pipeline import LlamaIndexPipeline

    return pipeline_module, storage_module, LlamaIndexPipeline


@pytest.mark.asyncio
async def test_stall_guard_raises_when_no_progress_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline_module, _, _ = _modules()
    monkeypatch.setattr(pipeline_module, "_INDEX_STALL_POLL_SECONDS", 0.05)

    def never_finishes() -> None:
        time.sleep(2)

    started = time.monotonic()
    with pytest.raises(pipeline_module.IndexingStallError, match="no progress"):
        await pipeline_module._run_with_stall_guard(
            never_finishes,
            stall_timeout=0.2,
        )
    assert time.monotonic() - started < 1


@pytest.mark.asyncio
async def test_stall_guard_returns_while_progress_keeps_flowing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline_module, _, _ = _modules()
    monkeypatch.setattr(pipeline_module, "_INDEX_STALL_POLL_SECONDS", 0.02)
    callback_slot: dict[str, Any] = {}
    monkeypatch.setattr(
        pipeline_module,
        "set_progress_callback",
        lambda callback: callback_slot.update(callback=callback),
    )

    def slow_but_moving() -> str:
        end = time.monotonic() + 0.35
        while time.monotonic() < end:
            callback_slot["callback"](1, 1)
            time.sleep(0.03)
        return "done"

    assert (
        await pipeline_module._run_with_stall_guard(
            slow_but_moving,
            stall_timeout=0.1,
        )
        == "done"
    )


def _make_pipeline(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Any:
    pipeline_module, _, pipeline_cls = _modules()
    monkeypatch.setattr(pipeline_module, "_INDEX_STALL_POLL_SECONDS", 0.05)
    monkeypatch.setattr(pipeline_module, "_INDEX_STALL_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(pipeline_cls, "_configure_settings", lambda self: None)

    async def _noop(*args, **kwargs) -> None:
        del args, kwargs

    async def _load(*args, **kwargs):
        del args, kwargs
        return [SimpleNamespace(text="hello")]

    monkeypatch.setattr(pipeline_cls, "_verify_embedding_connectivity", _noop)
    monkeypatch.setattr(
        pipeline_module,
        "LlamaIndexDocumentLoader",
        lambda logger: SimpleNamespace(load=_load),
    )
    return pipeline_cls(kb_base_dir=str(tmp_path), signature_provider=lambda: None)


@pytest.mark.asyncio
async def test_initialize_fails_bounded_when_create_index_stalls(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline_module, storage_module, _ = _modules()
    pipeline = _make_pipeline(tmp_path, monkeypatch)
    monkeypatch.setattr(storage_module, "create_index", lambda *a, **k: time.sleep(2))

    with pytest.raises(pipeline_module.IndexingStallError, match="no progress"):
        await pipeline.initialize("kb", ["doc.pdf"])


@pytest.mark.asyncio
async def test_initialize_succeeds_when_create_index_completes(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, storage_module, _ = _modules()
    pipeline = _make_pipeline(tmp_path, monkeypatch)
    monkeypatch.setattr(storage_module, "create_index", lambda *a, **k: 7)

    assert await pipeline.initialize("kb", ["doc.pdf"]) is True
