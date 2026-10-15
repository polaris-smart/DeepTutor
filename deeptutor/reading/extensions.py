"""Schema-driven extension protocol for Immersive Reading.

Extensions are Python entry points, never browser JavaScript. They receive a
server-verified reading context and return one of the small result schemas the
Reader knows how to render. One broken package is skipped without affecting
the reader or any other extension.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import logging
import threading
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, model_validator

from deeptutor.plugins.entry_points import load_entry_point_group

logger = logging.getLogger(__name__)
ENTRY_POINT_GROUP = "deeptutor.reading_extensions"
PROTOCOL_VERSION: Literal["1"] = "1"
RESULT_TYPES = frozenset({"card", "quiz", "feedback", "browser_speech"})
_ID_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"


class ReadingAction(BaseModel):
    id: str = Field(pattern=_ID_PATTERN)
    label: str = Field(min_length=1, max_length=80)
    trigger: Literal["toolbar"] = "toolbar"
    requires: list[Literal["selection", "visible_text"]] = Field(default_factory=list)


class ReadingExtensionManifest(BaseModel):
    id: str = Field(pattern=_ID_PATTERN)
    version: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=80)
    protocol_version: Literal["1"] = PROTOCOL_VERSION
    actions: list[ReadingAction] = Field(min_length=1, max_length=12)
    result_types: list[Literal["card", "quiz", "feedback", "browser_speech"]] = Field(min_length=1)


class ReadingContext(BaseModel):
    material_id: str
    locator: int = Field(ge=1)
    source_anchor: str = Field(default="", max_length=4096)
    locale: str = Field(default="en", max_length=32)
    selection: str = Field(default="", max_length=10_000)
    visible_text: str = Field(max_length=60_000)


class ReadingExtensionResult(BaseModel):
    type: Literal["card", "quiz", "feedback", "browser_speech"]
    title: str = Field(default="", max_length=160)
    message: str = Field(default="", max_length=4000)
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_payload(self) -> "ReadingExtensionResult":
        if len(json.dumps(self.payload, ensure_ascii=False, default=str)) > 64_000:
            raise ValueError("Reading extension result payload exceeds 64 KB.")
        if self.type == "browser_speech":
            text = self.payload.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 60_000:
                raise ValueError("browser_speech requires non-empty text up to 60,000 chars.")
        if self.type == "quiz":
            questions = self.payload.get("questions")
            if not isinstance(questions, list) or not 1 <= len(questions) <= 20:
                raise ValueError("quiz requires between 1 and 20 questions.")
            for row in questions:
                if not isinstance(row, dict):
                    raise ValueError("Each quiz question must be an object.")
                if not isinstance(row.get("prompt"), str):
                    raise ValueError("Each quiz question requires a prompt.")
                choices = row.get("choices")
                if not isinstance(choices, list) or not 2 <= len(choices) <= 8:
                    raise ValueError("Each quiz question requires 2 to 8 choices.")
                if not all(isinstance(choice, str) for choice in choices):
                    raise ValueError("Quiz choices must be strings.")
        return self


class ReadingExtension(Protocol):
    manifest: ReadingExtensionManifest

    def run_action(
        self, action: str, context: ReadingContext
    ) -> ReadingExtensionResult | dict[str, Any]: ...


def _coerce(name: str, loaded: Any) -> ReadingExtension | None:
    candidate = loaded() if isinstance(loaded, type) else loaded
    try:
        manifest = ReadingExtensionManifest.model_validate(getattr(candidate, "manifest", None))
    except Exception:
        logger.warning("Reading extension %r has an invalid manifest.", name, exc_info=True)
        return None
    if manifest.id != name:
        logger.warning("Reading extension %r declares a different id.", name)
        return None
    if not callable(getattr(candidate, "run_action", None)):
        logger.warning("Reading extension %r has no run_action method.", name)
        return None
    candidate.manifest = manifest
    return candidate


#: In-tree implementations of the entry points declared in pyproject.toml
#: (``[project.entry-points."deeptutor.reading_extensions"]``). Imported
#: lazily: these modules import from this one, so module-level imports here
#: would be circular.
_BUILTIN_READING_EXTENSIONS: tuple[tuple[str, str, str], ...] = (
    ("read_aloud", "deeptutor.reading.read_aloud", "ReadAloudExtension"),
    ("guided_learning", "deeptutor.reading.study_guidance", "StudyGuidanceExtension"),
    ("vocabulary", "deeptutor.reading.vocabulary", "VocabularyExtension"),
    ("quiz", "deeptutor.reading.quiz", "ReadingQuizExtension"),
    ("translation", "deeptutor.reading.translation", "TranslationExtension"),
)


def _load_builtin_reading_extensions(log: logging.Logger) -> list[ReadingExtension]:
    """Import the in-tree extensions the entry-point group declares.

    Containers ship deeptutor as copied source without package metadata, so
    ``importlib.metadata.entry_points`` resolves the group to nothing and
    every built-in reader extension would silently vanish (read-aloud 404
    实锤 2026-10). One broken module is skipped without affecting the rest,
    exactly like the entry-point path.
    """
    import importlib

    from types import SimpleNamespace

    from deeptutor.plugins.runtime import entry_point_can_load

    rows: list[ReadingExtension] = []
    for name, module_name, attr in _BUILTIN_READING_EXTENSIONS:
        try:
            # 与 entry-point 路径同一道审批门：插件被禁用/未审批时，builtin
            # 回退不得把它复活（registry-gating 契约，上游 #1222 后生效）。
            synthetic_ep = SimpleNamespace(
                name=name, value=f"{module_name}:{attr}", group=ENTRY_POINT_GROUP
            )
            if not entry_point_can_load(synthetic_ep, ENTRY_POINT_GROUP):
                log.warning("Built-in reading extension %r is gated; skipping.", name)
                continue
            module = importlib.import_module(module_name)
            row = _coerce(name, getattr(module, attr))
        except Exception:
            log.warning("Built-in reading extension %r failed to load.", name, exc_info=True)
            continue
        if row is not None:
            rows.append(row)
    return rows


class ReadingExtensionRegistry:
    def __init__(self, extensions: list[ReadingExtension] | None = None) -> None:
        rows = (
            extensions
            if extensions is not None
            else load_entry_point_group(ENTRY_POINT_GROUP, _coerce, log=logger)
        )
        if extensions is None and not rows:
            # Entry points unavailable (source-copy containers have no dist
            # metadata) — fall back to the in-tree implementations so the
            # reader keeps its built-in extensions instead of an empty
            # registry.
            rows = _load_builtin_reading_extensions(logger)
        self._extensions: dict[str, ReadingExtension] = {}
        for row in rows:
            self._extensions.setdefault(row.manifest.id, row)
        self._execution_lock = threading.Lock()
        self._executors: dict[str, ThreadPoolExecutor] = {}
        self._active: set[str] = set()
        self._timed_out: set[str] = set()

    def all(self) -> list[ReadingExtension]:
        return sorted(
            (
                row
                for row in self._extensions.values()
                if getattr(row, "_plugin_allowed", lambda: True)()
            ),
            key=lambda row: row.manifest.id,
        )

    def get(self, extension_id: str) -> ReadingExtension | None:
        row = self._extensions.get(extension_id)
        return row if row is not None and getattr(row, "_plugin_allowed", lambda: True)() else None

    def begin_action(self, extension_id: str, *, circuit_break: bool = True) -> bool:
        """Reserve one extension worker unless it is busy or circuit-broken.

        ``circuit_break`` is for sync handlers whose worker may still be stuck
        after a timeout. Async LLM actions are cancelled with the request, so
        a stale circuit must not block Quiz me until process restart.
        """
        with self._execution_lock:
            if extension_id in self._active:
                return False
            if circuit_break and extension_id in self._timed_out:
                return False
            self._active.add(extension_id)
            return True

    def finish_action(self, extension_id: str) -> None:
        with self._execution_lock:
            self._active.discard(extension_id)

    def mark_timed_out(self, extension_id: str) -> None:
        """Open the circuit: Python cannot safely kill a stuck sync handler."""
        with self._execution_lock:
            self._timed_out.add(extension_id)

    def executor_for(self, extension_id: str) -> ThreadPoolExecutor:
        """Return the extension's private single worker, never the global pool."""
        with self._execution_lock:
            executor = self._executors.get(extension_id)
            if executor is None:
                executor = ThreadPoolExecutor(
                    max_workers=1,
                    thread_name_prefix=f"reading-extension-{extension_id}",
                )
                self._executors[extension_id] = executor
            return executor

    def close(self) -> None:
        """Discard queued calls without waiting for an uncooperative handler."""
        with self._execution_lock:
            executors = list(self._executors.values())
            self._executors.clear()
        for executor in executors:
            executor.shutdown(wait=False, cancel_futures=True)


_registry: ReadingExtensionRegistry | None = None


def get_reading_extension_registry(*, refresh: bool = False) -> ReadingExtensionRegistry:
    global _registry
    if refresh and _registry is not None:
        _registry.close()
        _registry = None
    if _registry is None:
        _registry = ReadingExtensionRegistry()
    return _registry


__all__ = [
    "ENTRY_POINT_GROUP",
    "PROTOCOL_VERSION",
    "RESULT_TYPES",
    "ReadingAction",
    "ReadingContext",
    "ReadingExtensionManifest",
    "ReadingExtensionRegistry",
    "ReadingExtensionResult",
    "get_reading_extension_registry",
]
