from __future__ import annotations

from types import SimpleNamespace

from pydantic import ValidationError
import pytest

from deeptutor.reading.extensions import (
    ReadingAction,
    ReadingExtensionManifest,
    ReadingExtensionRegistry,
    ReadingExtensionResult,
)


def _extension(identifier: str = "sample"):
    return SimpleNamespace(
        manifest=ReadingExtensionManifest(
            id=identifier,
            version="1.0.0",
            name="Sample",
            actions=[ReadingAction(id="open", label="Open")],
            result_types=["card"],
        ),
        run_action=lambda *_: ReadingExtensionResult(type="card"),
    )


def test_registry_falls_back_to_builtins_when_no_entry_points_are_installed(
    monkeypatch,
):
    """Source-copy containers carry no package metadata, so the entry-point
    group resolves empty; the registry must then fall back to the in-tree
    extensions instead of silently losing read-aloud (N19 实锤 404)."""
    monkeypatch.setattr(
        "deeptutor.reading.extensions.load_entry_point_group",
        lambda *_args, **_kwargs: [],
    )
    rows = ReadingExtensionRegistry().all()
    ids = [row.manifest.id for row in rows]
    assert "read_aloud" in ids
    assert len(ids) >= 5


def test_builtin_fallback_skips_a_broken_module(monkeypatch):
    """One broken built-in module is skipped without losing the rest —
    the same isolation contract the entry-point path guarantees."""
    import deeptutor.reading.extensions as extensions_module

    monkeypatch.setattr(
        extensions_module,
        "_BUILTIN_READING_EXTENSIONS",
        (
            ("read_aloud", "deeptutor.reading.read_aloud", "ReadAloudExtension"),
            ("quiz", "deeptutor.reading.nonexistent_module_xyz", "Missing"),
        ),
    )
    rows = extensions_module._load_builtin_reading_extensions(
        extensions_module.logger
    )
    assert [row.manifest.id for row in rows] == ["read_aloud"]


def test_duplicate_extension_does_not_replace_the_first():
    first = _extension()
    duplicate = _extension()
    registry = ReadingExtensionRegistry([first, duplicate])
    assert registry.get("sample") is first


def test_result_schema_rejects_unsafe_or_unbounded_shapes():
    with pytest.raises(ValidationError):
        ReadingExtensionResult(type="browser_speech", payload={"text": ""})
    with pytest.raises(ValidationError):
        ReadingExtensionResult(
            type="quiz",
            payload={"questions": [{"prompt": "Question", "choices": ["one"]}]},
        )
    with pytest.raises(ValidationError):
        ReadingExtensionResult(type="card", payload={"body": "x" * 70_000})


def test_manifest_rejects_non_toolbar_triggers():
    with pytest.raises(ValidationError):
        ReadingAction(id="open", label="Open", trigger="javascript")  # type: ignore[arg-type]
