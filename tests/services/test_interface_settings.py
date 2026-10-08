"""Fork default regression — an unset ``interface.json`` means Chinese.

悦学 is a Chinese-first deployment: reader-facing generation follows
``DEFAULT_UI_SETTINGS``, so what the no-file default says decides what a
student who never opened settings receives. These two cases pin the fork's
default and prove an explicitly saved language still wins over it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deeptutor.services.settings import interface_settings


@pytest.fixture
def interface_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    settings_file = tmp_path / "interface.json"
    monkeypatch.setattr(
        interface_settings, "_interface_settings_file", lambda: settings_file
    )
    return settings_file


def test_missing_interface_json_defaults_to_chinese(interface_file: Path) -> None:
    settings = interface_settings.get_ui_settings()

    assert interface_file.exists() is False
    assert settings["language"] == "zh"
    assert settings["response_language"] == "zh"


def test_saved_language_is_not_overridden_by_default(interface_file: Path) -> None:
    interface_file.write_text(
        '{"theme": "snow", "language": "en", "response_language": "en"}',
        encoding="utf-8",
    )

    settings = interface_settings.get_ui_settings()

    assert settings["language"] == "en"
    assert settings["response_language"] == "en"
