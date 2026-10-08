"""Focused tests for the mastery-path composer question's failure surfacing.

The composer degrades to its static placeholder on any failure, so these
tests hold the two properties the degradation promise rests on: a hung or
unconfigured writer answers at once with a reason instead of stalling, and
the same stall is never paid twice within the negative cache's minute.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from deeptutor.services import mastery_hints
import deeptutor.services.llm as llm_module


@pytest.fixture(autouse=True)
def clear_hint_state() -> None:
    mastery_hints._cache.clear()
    mastery_hints._inflight.clear()
    mastery_hints._negative_cache.clear()


def _material() -> mastery_hints._Material:
    return mastery_hints._Material(
        path_name="Networking",
        goal="Understand how packets find their way",
        module_name="Routing",
        waypoint="Router intent",
        waypoint_type="concept",
        status="learning",
        transcript=[],
        anchor="kp-routing:3",
    )


@pytest.mark.asyncio
async def test_llm_timeout_sets_reason_and_negative_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hung LLM is cut off with a reason, and the repeat never pays twice."""

    async def collect(*_args: object) -> mastery_hints._Material:
        return _material()

    async def hang(**_kwargs: object) -> str:
        await asyncio.sleep(30)

    monkeypatch.setattr(mastery_hints, "_collect", collect)
    monkeypatch.setattr(llm_module, "complete", hang)
    monkeypatch.setattr(
        mastery_hints.hint_resilience, "task_profile_configured", lambda _kind: True
    )
    monkeypatch.setattr(mastery_hints, "_LLM_TIMEOUT", 0.05)

    started = time.monotonic()
    first = await mastery_hints.get_ask_hint("path-1", "session-1")
    elapsed = time.monotonic() - started

    assert first["hint"] == ""
    assert first["reason"] == "llm_timeout"
    assert first["knowledge_point_id"] == "kp-routing:3"
    assert elapsed < 12

    repeat_started = time.monotonic()
    second = await mastery_hints.get_ask_hint("path-1", "session-1")
    repeat_elapsed = time.monotonic() - repeat_started

    assert second == first
    assert repeat_elapsed < 1


@pytest.mark.asyncio
async def test_llm_failure_returns_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any other provider failure names itself instead of failing silently."""

    async def collect(*_args: object) -> mastery_hints._Material:
        return _material()

    async def broken(**_kwargs: object) -> str:
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(mastery_hints, "_collect", collect)
    monkeypatch.setattr(llm_module, "complete", broken)
    monkeypatch.setattr(
        mastery_hints.hint_resilience, "task_profile_configured", lambda _kind: True
    )
    monkeypatch.setattr(mastery_hints, "_LLM_TIMEOUT", 5.0)

    result = await mastery_hints.get_ask_hint("path-1", "session-1")

    assert result["hint"] == ""
    assert result["reason"] == "llm_unavailable"


@pytest.mark.asyncio
async def test_task_profile_not_configured_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No task profile means no wait: the empty answer comes back at once."""

    async def collect(*_args: object) -> mastery_hints._Material:
        return _material()

    async def must_not_run(**_kwargs: object) -> str:
        raise AssertionError("LLM must not be called without a task profile")

    monkeypatch.setattr(mastery_hints, "_collect", collect)
    monkeypatch.setattr(llm_module, "complete", must_not_run)
    monkeypatch.setattr(
        mastery_hints.hint_resilience, "task_profile_configured", lambda _kind: False
    )

    started = time.monotonic()
    result = await mastery_hints.get_ask_hint("path-1", "session-1")
    elapsed = time.monotonic() - started

    assert result["hint"] == ""
    assert result["reason"] == "task_profile_not_configured"
    assert elapsed < 1

    second = await mastery_hints.get_ask_hint("path-1", "session-1")

    assert second == result


@pytest.mark.asyncio
async def test_successful_hint_is_unchanged_and_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The working path still writes its question and still caches it."""

    async def collect(*_args: object) -> mastery_hints._Material:
        return _material()

    calls = 0

    async def write(**_kwargs: object) -> str:
        nonlocal calls
        calls += 1
        return "Why does the router need the intent and not just the query?"

    monkeypatch.setattr(mastery_hints, "_collect", collect)
    monkeypatch.setattr(llm_module, "complete", write)
    monkeypatch.setattr(
        mastery_hints.hint_resilience, "task_profile_configured", lambda _kind: True
    )

    first = await mastery_hints.get_ask_hint("path-1", "session-1")
    second = await mastery_hints.get_ask_hint("path-1", "session-1")

    assert first == second
    assert first["hint"] == "Why does the router need the intent and not just the query?"
    assert first["reason"] == ""
    assert calls == 1
