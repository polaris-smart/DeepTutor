"""LlamaIndex embedding adapter backed by DeepTutor's embedding service."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
import logging
import threading
from typing import Any, Callable, List

from llama_index.core import Settings
from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.bridge.pydantic import PrivateAttr

from deeptutor.services.config.provider_runtime import EMBEDDING_PROVIDERS
from deeptutor.services.embedding import EmbeddingConfig, get_embedding_client, get_embedding_config
from deeptutor.services.embedding.config import scoped_embedding_config
from deeptutor.services.embedding.validation import validate_embedding_batch

from .config import chunk_geometry

_LLAMAINDEX_EMBED_GROUP_ITEMS = 256


@dataclass
class _IndexingProgress:
    total_batches: int
    provider_batch_size: int
    completed_batches: int = 0


# An executor thread inherits the indexing operation's context. Keep both the
# callback and its cumulative count there: a timed-out worker must never pick
# up a later task's callback from a shared CustomEmbedding instance (#1478).
_task_progress_callback: ContextVar[Callable[[int, int], None] | None] = ContextVar(
    "llamaindex_progress_callback", default=None
)
_indexing_progress: ContextVar[_IndexingProgress | None] = ContextVar(
    "llamaindex_indexing_progress", default=None
)


def _config_fingerprint(config: EmbeddingConfig) -> tuple[Any, ...]:
    """Return the settings fields that affect LlamaIndex embedding behavior."""
    return (
        getattr(config, "binding", None),
        getattr(config, "model", None),
        getattr(config, "dim", None),
        getattr(config, "effective_url", None) or getattr(config, "base_url", None),
        getattr(config, "api_version", None),
        getattr(config, "send_dimensions", None),
    )


def _effective_request_batch_size(config: Any) -> int:
    configured = max(1, int(getattr(config, "batch_size", 1) or 1))
    binding = str(getattr(config, "binding", "") or "").strip().lower()
    spec = EMBEDDING_PROVIDERS.get(binding)
    provider_max = spec.max_batch_items if spec else 256
    return min(configured, provider_max)


def _aligned_llamaindex_batch_size(config: Any) -> int:
    """Group nodes in provider-batch multiples to avoid short requests."""
    request_batch_size = _effective_request_batch_size(config)
    multiplier = max(1, _LLAMAINDEX_EMBED_GROUP_ITEMS // request_batch_size)
    return request_batch_size * multiplier


class CustomEmbedding(BaseEmbedding):
    """Custom LlamaIndex embedding adapter for DeepTutor embedding providers."""

    _client: Any = PrivateAttr()
    _logger: Any = PrivateAttr()
    _progress_callback: Any = PrivateAttr(default=None)
    _binding: Any = PrivateAttr(default=None)
    _model: Any = PrivateAttr(default=None)
    _fingerprint: Any = PrivateAttr(default=None)
    _batch_progress: Any = PrivateAttr(default_factory=threading.local)

    def __init__(self, **kwargs):
        progress_cb = kwargs.pop("progress_callback", None)
        embedding_config = kwargs.pop("embedding_config", None)
        client = (
            get_embedding_client(embedding_config)
            if embedding_config is not None
            else get_embedding_client()
        )
        kwargs.setdefault(
            "embed_batch_size",
            _aligned_llamaindex_batch_size(getattr(client, "config", embedding_config)),
        )
        super().__init__(**kwargs)
        self._logger = logging.getLogger(__name__)
        self._progress_callback = progress_cb
        self._bind_client(client)

    def _bind_client(self, client: Any) -> None:
        self._client = client
        client_config = getattr(self._client, "config", None)
        self._binding = getattr(client_config, "binding", None)
        self._model = getattr(client_config, "model", None)
        self._fingerprint = (
            _config_fingerprint(client_config) if client_config is not None else None
        )

    def matches_config(self, config: EmbeddingConfig) -> bool:
        """Return whether this adapter was created for the active config."""
        return self._fingerprint == _config_fingerprint(config)

    def refresh_client(self, config: EmbeddingConfig | None = None) -> Any:
        """Refresh the cached client if settings changed while the pipeline lived."""
        client = get_embedding_client(config) if config is not None else get_embedding_client()
        if client is not self._client:
            self._bind_client(client)
        return self._client

    def set_progress_callback(self, callback):
        """Set progress callback fn(batch_num, total_batches)."""
        self._progress_callback = callback

    def _client_batch_size(self) -> int:
        """Return the effective request batch size used by EmbeddingClient."""
        return _effective_request_batch_size(getattr(self._client, "config", None))

    def _request_count(self, item_count: int) -> int:
        batch_size = self._client_batch_size()
        return (max(0, item_count) + batch_size - 1) // batch_size

    def __call__(self, nodes, **kwargs):
        """Count provider batches across the full split-node set once.

        K12 merge: upstream ContextVar channel (#1478 worker isolation) for the
        pipeline path; our direct ``get_text_embedding_batch`` channel below
        keeps standalone calls monotonic. ``_aget_text_embeddings`` reports via
        exactly one channel (ContextVar wins).
        """
        callback = _task_progress_callback.get() or self._progress_callback
        if callback is None or not nodes:
            return super().__call__(nodes, **kwargs)
        config = getattr(self._client, "config", None)
        binding = getattr(config, "binding", "")
        provider = EMBEDDING_PROVIDERS.get(binding)
        provider_limit = provider.max_batch_items if provider else 256
        batch_size = max(1, min(getattr(config, "batch_size", 10), provider_limit))
        outer_size = self.embed_batch_size
        total_batches = sum(
            (min(outer_size, len(nodes) - start) + batch_size - 1) // batch_size
            for start in range(0, len(nodes), outer_size)
        )
        token = _indexing_progress.set(
            _IndexingProgress(total_batches=total_batches, provider_batch_size=batch_size)
        )
        try:
            return super().__call__(nodes, **kwargs)
        finally:
            _indexing_progress.reset(token)

    def get_text_embedding_batch(
        self,
        texts: List[str],
        show_progress: bool = False,
        **kwargs: Any,
    ) -> List[List[float]]:
        """Report nested provider batches as one monotonic indexing sequence.

        LlamaIndex first splits ``texts`` by ``embed_batch_size`` and then
        ``EmbeddingClient`` splits every one of those chunks again by the
        configured provider batch size. The provider callback therefore emits
        local sequences such as ``1/3, 2/3, 3/3`` hundreds of times. Treating
        those local values as whole-index progress makes the UI repeatedly hit
        100% while embedding is still running. (K12 fork: covers callers that
        bypass ``__call__``; the pipeline path is handled by the ContextVar.)
        """
        outer_batch_size = max(1, int(self.embed_batch_size))
        total_requests = sum(
            self._request_count(min(outer_batch_size, len(texts) - start))
            for start in range(0, len(texts), outer_batch_size)
        )
        state = self._batch_progress
        state.active = True
        state.completed = 0
        state.total = total_requests
        try:
            return super().get_text_embedding_batch(
                texts,
                show_progress=show_progress,
                **kwargs,
            )
        finally:
            state.active = False

    @classmethod
    def class_name(cls) -> str:
        return "custom_embedding"

    def _run_in_new_loop(self, coro):
        """Run an async coroutine from sync context using a fresh event loop."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    async def _aget_query_embedding(self, query: str) -> List[float]:
        client = self.refresh_client()
        embeddings = await client.embed([query], input_type="search_query")
        return validate_embedding_batch(
            embeddings,
            expected_count=1,
            binding=self._binding,
            model=self._model,
        )[0]

    async def _aget_text_embedding(self, text: str) -> List[float]:
        client = self.refresh_client()
        embeddings = await client.embed([text], input_type="search_document")
        return validate_embedding_batch(
            embeddings,
            expected_count=1,
            binding=self._binding,
            model=self._model,
        )[0]

    async def _aget_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        client = self.refresh_client()
        # K12 merge: ContextVar channel (upstream, pipeline path) wins when
        # active; otherwise our threading.local channel covers direct calls.
        # Exactly one channel wraps the callback so progress is never doubled.
        callback = _task_progress_callback.get() or self._progress_callback
        progress = _indexing_progress.get()
        state = self._batch_progress
        state_active = bool(getattr(state, "active", False))
        completed_before = int(getattr(state, "completed", 0))
        if callback is not None and progress is not None:
            offset = progress.completed_batches
            sink = callback

            def report(batch_num: int, _total_batches: int) -> None:
                sink(offset + batch_num, progress.total_batches)

            callback = report
        elif callback is not None and state_active:

            def _report_direct(current: int, total: int) -> None:
                callback(completed_before + current, int(state.total))

            callback = _report_direct
        embeddings = await client.embed(
            texts,
            progress_callback=callback,
            input_type="search_document",
        )
        if progress is not None:
            progress.completed_batches += (
                len(texts) + progress.provider_batch_size - 1
            ) // progress.provider_batch_size
        if state_active:
            state.completed = completed_before + self._request_count(len(texts))
        return validate_embedding_batch(
            embeddings,
            expected_count=len(texts),
            binding=self._binding,
            model=self._model,
        )

    def _get_query_embedding(self, query: str) -> List[float]:
        return self._run_in_new_loop(self._aget_query_embedding(query))

    def _get_text_embedding(self, text: str) -> List[float]:
        return self._run_in_new_loop(self._aget_text_embedding(text))

    def _get_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        self._logger.info(f"Embedding {len(texts)} text chunks...")
        result = self._run_in_new_loop(self._aget_text_embeddings(texts))
        self._logger.info(f"Embedding complete: {len(result)} vectors")
        return result


_operation_adapter: ContextVar[CustomEmbedding | None] = ContextVar(
    "llamaindex_embedding", default=None
)


def current_embedding():
    if scoped_embedding_config() is not None and _operation_adapter.get() is not None:
        return _operation_adapter.get()
    return Settings.embed_model


def configure_llamaindex_settings(logger=None) -> None:
    """Configure LlamaIndex globals for DeepTutor's current embedding config."""
    embedding_cfg = get_embedding_config()

    current = (
        None if scoped_embedding_config() is not None else getattr(Settings, "_embed_model", None)
    )
    configured = False
    if isinstance(current, CustomEmbedding) and current.matches_config(embedding_cfg):
        current.refresh_client(embedding_cfg)
    else:
        adapter = CustomEmbedding(embedding_config=embedding_cfg)
        if scoped_embedding_config() is not None:
            _operation_adapter.set(adapter)
        else:
            Settings.embed_model = adapter
        configured = True
    chunk_size, chunk_overlap = chunk_geometry()
    Settings.chunk_size = chunk_size
    Settings.chunk_overlap = chunk_overlap

    if logger is not None:
        message = (
            f"LlamaIndex configured: embedding={embedding_cfg.model} "
            f"({embedding_cfg.dim}D, {embedding_cfg.binding}), chunk_size={chunk_size}"
        )
        if configured:
            logger.info(message)
        else:
            logger.debug(message)


def set_progress_callback(callback) -> None:
    """Bind a callback to this indexing context, not a shared adapter slot."""
    _task_progress_callback.set(callback)


async def verify_embedding_connectivity(logger=None) -> None:
    """Quick smoke-test to catch embedding config/network issues before indexing."""
    if logger is not None:
        logger.info("Verifying embedding API connectivity...")
    try:
        client = get_embedding_client()
        result = await client.embed(["connectivity test"])
        validated = validate_embedding_batch(
            result,
            expected_count=1,
            binding=getattr(client.config, "binding", None),
            model=getattr(client.config, "model", None),
        )
        if logger is not None:
            logger.info(f"Embedding API OK (returned {len(validated[0])}-dim vector)")
    except Exception as exc:
        if logger is not None:
            logger.error(f"Embedding API connectivity check failed: {exc}")
        raise RuntimeError(
            f"Cannot reach embedding API. Please check your embedding configuration. Error: {exc}"
        ) from exc
