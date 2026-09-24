"""Ukrainian is a first-class UI/output language, not a half-wired one.

Language support in this codebase was historically a binary — ``zh`` was the
special case and ``en`` the silent fallback — repeated independently in the
settings normalizer, the settings spec, the CLI banner, the prompt language
directive and the quiz judge. Adding a language meant editing all of them, and
missing one produced no error: the user picked Ukrainian and got English back.

These tests pin every seam a new language has to pass through, so the next one
fails loudly in CI instead of quietly in the UI.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from deeptutor.api.routers.quiz_judge import (
    _JUDGE_SYSTEM_PROMPTS,
    SUPPORTED_JUDGE_LANGUAGES,
)
from deeptutor.runtime.banner import LABELS, labels_for
from deeptutor.services.config.settings_spec import _LANGUAGE_CHOICES
from deeptutor.services.prompt.language import language_directive, language_label
from deeptutor.services.settings.interface_settings import _normalize_language

WEB = pathlib.Path(__file__).resolve().parents[2] / "web"


@pytest.mark.parametrize("raw", ["uk", "UK", " uk ", "ukrainian", "ua", "uk-UA"])
def test_normalizer_accepts_ukrainian_spellings(raw: str) -> None:
    assert _normalize_language(raw) == "uk"


def test_an_unknown_language_still_falls_back() -> None:
    assert _normalize_language("klingon") == "en"


def test_settings_offers_ukrainian() -> None:
    assert "uk" in {code for code, _label, _desc in _LANGUAGE_CHOICES}


def test_cli_banner_is_fully_translated() -> None:
    """A partial label set would render a half-Ukrainian wizard."""
    assert set(LABELS["uk"]) == set(LABELS["en"])
    assert labels_for("uk") is LABELS["uk"]


def test_language_directive_names_ukrainian() -> None:
    assert language_label("uk") == "Українська"
    assert "Українська" in language_directive("uk")


def test_judge_speaks_every_language_it_advertises() -> None:
    """The whitelist is derived from the prompts, so the two cannot drift."""
    assert SUPPORTED_JUDGE_LANGUAGES == frozenset(_JUDGE_SYSTEM_PROMPTS)
    assert "uk" in SUPPORTED_JUDGE_LANGUAGES
    assert "українською" in _JUDGE_SYSTEM_PROMPTS["uk"]


def test_web_locale_has_core_copy_and_english_fallback() -> None:
    """Missing Ukrainian keys resolve through i18next's English bundle."""
    uk = json.loads((WEB / "locales/uk/app.json").read_text(encoding="utf-8"))
    assert {"language.ukrainian", "Start", "Model output language"} <= set(uk)
    init = (WEB / "i18n/init.ts").read_text(encoding="utf-8")
    assert 'fallbackLng: "en"' in init


#: K12 fork: English keys added by the fork (K12 mastery/assignment/class
#: flows) that have no Ukrainian translation yet. At runtime i18next falls
#: back to English for these (fallbackLng: "en"). Upstream keys must stay
#: covered 1:1; when a fork key gets translated, remove it from this list.
_K12_FORK_KEYS_UNTRANSLATED = frozenset({
    "Already signed in as",
    "Ask a question when you're ready.",
    "Assign",
    "Assign homework from your question bank and review class results",
    "Assigned homework",
    "Assigned models",
    "Assignment created",
    "Assignment title",
    "Assignments",
    "Assignments tooltip",
    "Assignments you create will appear here with class statistics.",
    "Book ID",
    "Can edit",
    "Can read",
    "Change line width",
    "Checking access…",
    "Choice",
    "Class",
    "Couldn't load memory settings",
    "DeepTutor Drafting…",
    "DeepTutor Planning…",
    "DeepTutor Reasoning…",
    "DeepTutor Responding…",
    "Delete failed.",
    "Delete topic “{{name}}”?",
    "Diagnosing",
    "Drag to resize · double-click to reset",
    "Due date (optional)",
    "Due {{date}}",
    "Enter a knowledge base name and a book ID",
    "Enter a title and select at least one knowledge point",
    "Error Diagnosis",
    "Error review",
    "Explaining",
    "Failed to create assignment",
    "Failed to load assignments",
    "Failed to load knowledge points",
    "Failed to load pipeline status",
    "Failed to retry stage",
    "Failed to start pipeline",
    "Family tooltip",
    "Finish the first diagnostic exercise to enter the learning stage",
    "Finishing the current operation safely…",
    "Generate modules from a book",
    "Guide",
    "Import a textbook to build the route, or delete this topic and start over.",
    "Ingest pipeline",
    "KP mapping",
    "Knowledge points",
    "Larger text",
    "Learning",
    "Learning goals",
    "Loading failed.",
    "Material pipeline",
    "Module Test",
    "My Children",
    "New assignment",
    "No assigned models",
    "No assignments yet",
    "No choice question bound",
    "No knowledge points in this book yet",
    "No pipeline runs yet",
    "Not shared with anyone yet.",
    "Not submitted",
    "Opening a secure connection…",
    "Overall",
    "Per knowledge point",
    "Per question type",
    "Pick a textbook — each chapter becomes a module, and its learning objectives become the knowledge points.",
    "Pick a textbook — its own chapter tree becomes your first mastery path, no goal writing needed.",
    "Pipeline stages",
    "Polling status…",
    "Practicing",
    "Question bank generation",
    "Question bank mounting",
    "Reconnect",
    "Regenerate response",
    "Reply below to continue this turn.",
    "Resize the reader",
    "Response interrupted",
    "Retrieval Practice",
    "Retry this stage",
    "Reviewing",
    "Run the four-stage material pipeline: structure check, question bank generation, KP mapping, mounting",
    "Run {{run_id}}",
    "Select a book",
    "Select a book first",
    "Select a class",
    "Select a class first",
    "Select a user",
    "Share",
    "Share book",
    "Share with",
    "Shared with",
    "Short answer",
    "Show answer",
    "Signing out…",
    "Skipped {{count}} knowledge points without an assignable question",
    "Smaller text",
    "Start a run to watch the four stages execute and retry any failed one.",
    "Start from your textbook",
    "Start pipeline",
    "Starting…",
    "Stopping",
    "Structure check",
    "Student",
    "Switch account",
    "Textbook progress",
    "The response is complete.",
    "The tutor is preparing a response.",
    "This book has no chapters to import yet.",
    "This topic has no modules yet.",
    "This turn was stopped.",
    "Turn failed",
    "Waiting for your answer",
    "Working",
    "Your books already carry a full chapter tree — start a path from one of them, or tell DeepTutor what you want to learn instead.",
    "Your request is waiting for a worker.",
    "Your response is safe. Restoring the live connection…",
    "done",
    "failed",
    "queued",
    "skipped",
    "{{count}} objectives",
    "{{count}} questions",
    "{{count}} selectable",
    "{{mastery}}% mastered",
    "{{name}} Drafting…",
    "{{name}} Responding…",
    "{{submitted}}/{{total}} submitted",
    "{{unit}} {{n}} of {{total}}",
})


def test_web_locale_covers_every_english_key() -> None:
    """Cover English keys plus Ukrainian's extra few/many plural forms.

    K12 fork: fork-added English keys are exempted via the explicit
    allowlist above; they resolve to English at runtime. When the allowlist
    stops covering a missing key the assertion fails — add the translation
    or extend the list consciously.
    """
    en = json.loads((WEB / "locales/en/app.json").read_text(encoding="utf-8"))
    uk = json.loads((WEB / "locales/uk/app.json").read_text(encoding="utf-8"))
    assert set(en) - set(uk) <= _K12_FORK_KEYS_UNTRANSLATED
    assert all(key.endswith(("_few", "_many")) for key in set(uk) - set(en))


def test_web_locale_keeps_interpolation_placeholders() -> None:
    import re

    en = json.loads((WEB / "locales/en/app.json").read_text(encoding="utf-8"))
    uk = json.loads((WEB / "locales/uk/app.json").read_text(encoding="utf-8"))
    pattern = re.compile(r"\{\{[^}]+\}\}")
    mismatches = [
        key
        for key in set(en) & set(uk)
        if set(pattern.findall(en[key])) != set(pattern.findall(uk[key]))
    ]
    assert mismatches == []


def test_frontend_language_list_is_the_single_source() -> None:
    """Pickers must map over APP_LANGUAGES rather than inline a literal."""
    languages = (WEB / "i18n/languages.ts").read_text(encoding="utf-8")
    assert '{ code: "uk", labelKey: "language.ukrainian" }' in languages
    overview = (WEB / "components/settings/SettingsOverview.tsx").read_text(encoding="utf-8")
    assert "APP_LANGUAGES.map" in overview
    assert '["en", "zh"]' not in overview
