"""Focused tests for Immersive Reading's dynamic composer question."""

from __future__ import annotations

import asyncio
import time

import pytest

from deeptutor.services import reading_hints
import deeptutor.services.llm as llm_module


@pytest.fixture(autouse=True)
def clear_hint_state() -> None:
    reading_hints._cache.clear()
    reading_hints._inflight.clear()
    reading_hints._negative_cache.clear()


def _material() -> reading_hints._Material:
    return reading_hints._Material(
        material_id="material-1",
        title="Residual Networks",
        render_mode="pdf",
        locator=7,
        unit_text="A residual block adds its input to the transformed signal.",
        selection="",
        transcript=[],
        transcript_length=2,
    )


def test_sanitize_rejects_non_question_output() -> None:
    assert reading_hints._sanitize("Residual connections stabilize gradients.", "en") == ""
    assert (
        reading_hints._sanitize(
            "Why do I need residual connections?",
            "en",
            "Residual connections are needed to stabilize gradients.",
        )
        == ""
    )
    assert reading_hints._sanitize("Why do I need a residual connection here?", "en")


@pytest.mark.asyncio
async def test_cache_hit_does_not_invoke_llm_again(monkeypatch: pytest.MonkeyPatch) -> None:
    material = _material()
    calls = 0

    async def collect(*_args: object) -> reading_hints._Material:
        return material

    async def call_llm(_material: reading_hints._Material, _language: str) -> str:
        nonlocal calls
        calls += 1
        return "Why do I need a residual connection here?"

    monkeypatch.setattr(reading_hints, "_collect", collect)
    monkeypatch.setattr(reading_hints, "_call_llm", call_llm)
    monkeypatch.setattr(reading_hints, "_response_language", lambda: "en")
    monkeypatch.setattr(
        reading_hints.hint_resilience, "task_profile_configured", lambda _kind: True
    )

    first = await reading_hints.get_ask_hint("workspace-1", locator=7)
    second = await reading_hints.get_ask_hint("workspace-1", locator=7)

    assert first == second
    assert first["hint"] == "Why do I need a residual connection here?"
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [asyncio.TimeoutError(), RuntimeError("model unavailable")],
    ids=["timeout", "failure"],
)
async def test_llm_failure_returns_empty_hint(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    async def collect(*_args: object) -> reading_hints._Material:
        return _material()

    async def fail_llm(_material: reading_hints._Material, _language: str) -> str:
        raise error

    monkeypatch.setattr(reading_hints, "_collect", collect)
    monkeypatch.setattr(reading_hints, "_call_llm", fail_llm)
    monkeypatch.setattr(reading_hints, "_response_language", lambda: "en")
    monkeypatch.setattr(
        reading_hints.hint_resilience, "task_profile_configured", lambda _kind: True
    )

    result = await reading_hints.get_ask_hint("workspace-1", locator=7)

    assert result["hint"] == ""
    assert result["material_id"] == "material-1"


@pytest.mark.asyncio
async def test_llm_timeout_sets_reason_and_negative_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hung LLM is cut off with a reason, and the same ask never pays twice."""

    async def collect(*_args: object) -> reading_hints._Material:
        return _material()

    async def hang(**_kwargs: object) -> str:
        await asyncio.sleep(30)

    monkeypatch.setattr(reading_hints, "_collect", collect)
    monkeypatch.setattr(llm_module, "complete", hang)
    monkeypatch.setattr(reading_hints, "_response_language", lambda: "en")
    monkeypatch.setattr(
        reading_hints.hint_resilience, "task_profile_configured", lambda _kind: True
    )
    monkeypatch.setattr(reading_hints, "_LLM_TIMEOUT", 0.05)

    started = time.monotonic()
    first = await reading_hints.get_ask_hint("workspace-timeout", locator=7)
    elapsed = time.monotonic() - started

    assert first["hint"] == ""
    assert first["reason"] == "llm_timeout"
    assert first["material_id"] == "material-1"
    assert elapsed < 12

    repeat_started = time.monotonic()
    second = await reading_hints.get_ask_hint("workspace-timeout", locator=7)
    repeat_elapsed = time.monotonic() - repeat_started

    assert second == first
    assert repeat_elapsed < 1


@pytest.mark.asyncio
async def test_task_profile_not_configured_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No task profile means no wait: the empty answer comes back at once."""

    async def collect(*_args: object) -> reading_hints._Material:
        return _material()

    async def must_not_run(**_kwargs: object) -> str:
        raise AssertionError("LLM must not be called without a task profile")

    monkeypatch.setattr(reading_hints, "_collect", collect)
    monkeypatch.setattr(llm_module, "complete", must_not_run)
    monkeypatch.setattr(
        reading_hints.hint_resilience, "task_profile_configured", lambda _kind: False
    )

    started = time.monotonic()
    result = await reading_hints.get_ask_hint("workspace-noprofile", locator=7)
    elapsed = time.monotonic() - started

    assert result["hint"] == ""
    assert result["reason"] == "task_profile_not_configured"
    assert elapsed < 1

    second = await reading_hints.get_ask_hint("workspace-noprofile", locator=7)

    assert second == result


@pytest.mark.asyncio
async def test_llm_call_requests_roomy_token_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Thinking models burn completion budget reasoning before answering.

    The hint question is short, but the reasoning chain is not — the call
    must reserve enough completion tokens for both or the answer is truncated
    to an empty string.
    """

    async def collect(*_args: object) -> reading_hints._Material:
        return _material()

    captured: dict[str, object] = {}

    async def spy(**kwargs: object) -> str:
        captured.update(kwargs)
        return "Why do I need a residual connection here?"

    monkeypatch.setattr(reading_hints, "_collect", collect)
    monkeypatch.setattr(llm_module, "complete", spy)
    monkeypatch.setattr(reading_hints, "_response_language", lambda: "en")
    monkeypatch.setattr(
        reading_hints.hint_resilience, "task_profile_configured", lambda _kind: True
    )

    result = await reading_hints.get_ask_hint("workspace-budget", locator=7)

    assert result["hint"] == "Why do I need a residual connection here?"
    assert captured["max_tokens"] >= 2048
