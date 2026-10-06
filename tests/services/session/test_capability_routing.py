"""Regression coverage for pre-execution chat quiz routing (#807)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from deeptutor.core.stream import StreamEvent, StreamEventType
from deeptutor.services.session.sqlite_store import SQLiteSessionStore
from deeptutor.services.session.turn_runtime import TurnRuntimeManager
from deeptutor.services.skill.service import SkillService


async def _noop_async(*_args, **_kwargs):
    return None


def _fake_persona_service() -> SimpleNamespace:
    return SimpleNamespace(load_for_context=lambda _name: "")


def _configure_runtime(monkeypatch: pytest.MonkeyPatch, captured: dict, tmp_path: Path) -> None:
    class FakeContextBuilder:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def build(self, **_kwargs):
            return SimpleNamespace(
                conversation_history=[],
                conversation_summary="",
                context_text="",
                token_count=0,
                budget=0,
            )

    class FakeOrchestrator:
        async def handle(self, context):
            captured["active_capability"] = context.active_capability
            captured["metadata"] = context.metadata
            yield StreamEvent(
                type=StreamEventType.CONTENT,
                content="ok",
                metadata={"call_kind": "llm_final_response"},
            )
            yield StreamEvent(
                type=StreamEventType.DONE,
                source=context.active_capability,
                metadata={},
            )

    monkeypatch.setattr(
        "deeptutor.services.config.runtime_settings.load_system_settings",
        lambda: {"capability_routing_enabled": captured["global_enabled"]},
    )
    monkeypatch.setattr("deeptutor.services.llm.config.get_llm_config", lambda: SimpleNamespace())
    monkeypatch.setattr(
        "deeptutor.services.session.context_builder.ContextBuilder", FakeContextBuilder
    )
    monkeypatch.setattr("deeptutor.runtime.orchestrator.ChatOrchestrator", FakeOrchestrator)
    monkeypatch.setattr(
        "deeptutor.services.memory.get_memory_store",
        lambda: SimpleNamespace(read_l3_concat=lambda: "", emit=_noop_async),
    )
    monkeypatch.setattr(
        "deeptutor.services.skill.get_skill_service",
        lambda: SkillService(root=tmp_path / "skills", builtin_root=None),
    )
    monkeypatch.setattr("deeptutor.services.persona.get_persona_service", _fake_persona_service)


async def _run_quiz_turn(tmp_path, captured: dict, config: dict | None = None):
    runtime = TurnRuntimeManager(SQLiteSessionStore(tmp_path / "routing.db"))
    session, turn = await runtime.start_turn(
        {
            "type": "start_turn",
            "content": "Please generate 3 quiz questions",
            "session_id": None,
            "capability": "chat",
            "tools": ["web_search", "brainstorm"],
            "knowledge_bases": [],
            "attachments": [],
            "language": "en",
            "config": config or {},
        }
    )
    events = []
    async for _event in runtime.subscribe_turn(turn["id"], after_seq=0):
        events.append(_event)
    done = next(event for event in events if event["type"] == "done")
    captured["done_metadata"] = done["metadata"]
    detail = await runtime.store.get_session(session["id"])
    assert detail is not None
    return turn, detail


@pytest.mark.asyncio
async def test_quiz_requests_stay_in_chat_by_default(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {"global_enabled": False}
    _configure_runtime(monkeypatch, captured, tmp_path)

    turn, session = await _run_quiz_turn(tmp_path, captured)

    assert turn["capability"] == "chat"
    assert captured["active_capability"] == "chat"
    assert captured["metadata"]["capability_route"] is None
    assert "capability_route" not in captured["done_metadata"]
    assert session["preferences"]["capability"] == "chat"


@pytest.mark.asyncio
async def test_enabled_explicit_quiz_routes_for_one_turn(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {"global_enabled": True}
    _configure_runtime(monkeypatch, captured, tmp_path)

    turn, session = await _run_quiz_turn(tmp_path, captured)

    assert turn["capability"] == "deep_question"
    assert captured["active_capability"] == "deep_question"
    assert captured["metadata"]["capability_route"]["auto_routed"] is True
    assert captured["metadata"]["capability_route"]["strategy"] == "rule"
    assert captured["done_metadata"]["capability_route"]["capability"] == "deep_question"
    assert session["preferences"]["capability"] == "chat"


@pytest.mark.asyncio
async def test_auto_route_false_overrides_global_setting(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {"global_enabled": True}
    _configure_runtime(monkeypatch, captured, tmp_path)

    turn, session = await _run_quiz_turn(tmp_path, captured, {"auto_route": False})

    assert turn["capability"] == "chat"
    assert captured["active_capability"] == "chat"
    assert session["preferences"]["capability"] == "chat"


@pytest.mark.asyncio
async def test_per_turn_flag_opts_in_when_global_default_is_off(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {"global_enabled": False}
    _configure_runtime(monkeypatch, captured, tmp_path)

    turn, session = await _run_quiz_turn(tmp_path, captured, {"auto_route": True})

    assert turn["capability"] == "deep_question"
    assert captured["active_capability"] == "deep_question"
    assert session["preferences"]["capability"] == "chat"


@pytest.mark.asyncio
async def test_explicit_visualize_ask_routes_without_routing_flag(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N4: "画二次函数图像" in plain chat must reach the visualize capability
    even with the global routing flag off — the canvas is the only channel
    that renders the ask. The conversation itself stays in chat."""
    captured: dict = {"global_enabled": False}
    _configure_runtime(monkeypatch, captured, tmp_path)
    runtime = TurnRuntimeManager(SQLiteSessionStore(tmp_path / "viz.db"))

    session, turn = await runtime.start_turn(
        {
            "type": "start_turn",
            "content": "画二次函数y=x^2的图像",
            "session_id": None,
            "capability": "chat",
            "tools": ["web_search", "brainstorm"],
            "knowledge_bases": [],
            "attachments": [],
            "language": "zh",
            "config": {},
        }
    )
    async for _event in runtime.subscribe_turn(turn["id"], after_seq=0):
        pass

    assert turn["capability"] == "visualize"
    assert captured["active_capability"] == "visualize"
    assert captured["metadata"]["capability_route"]["strategy"] == "rule"
    assert captured["metadata"]["capability_route"]["capability"] == "visualize"
    detail = await runtime.store.get_session(session["id"])
    assert detail is not None
    assert detail["preferences"]["capability"] == "chat"


def test_visualize_route_rejects_non_ask_and_non_chat() -> None:
    from deeptutor.runtime.capability_routing import route_explicit_visualize_request

    # Explicit ask routes.
    route = route_explicit_visualize_request("帮我画一个函数图像", "chat")
    assert route is not None and route.capability == "visualize"
    # Non-ask content stays in chat.
    assert route_explicit_visualize_request("帮我总结一下这篇课文", "chat") is None
    # Explicit non-chat capability is never overridden.
    assert route_explicit_visualize_request("画一个函数图像", "deep_solve") is None
    # Workspace turns are never routed.
    assert (
        route_explicit_visualize_request("画一个函数图像", "chat", workspace_mode="reading")
        is None
    )


def test_visualize_route_respects_learning_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    import deeptutor.multi_user.learning_access as learning_access
    from deeptutor.runtime.capability_routing import route_explicit_visualize_request

    monkeypatch.setattr(
        learning_access,
        "current_learning_policy",
        lambda: {"allowed_capabilities": ["chat", "immersive_reading"]},
        raising=True,
    )
    assert route_explicit_visualize_request("画一个函数图像", "chat") is None

    monkeypatch.setattr(
        learning_access,
        "current_learning_policy",
        lambda: {"allowed_capabilities": ["chat", "visualize"]},
        raising=True,
    )
    route = route_explicit_visualize_request("画一个函数图像", "chat")
    assert route is not None and route.capability == "visualize"


def test_visualize_route_routes_staff_without_learning_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N4 v1011: teacher/admin plain chat carries no learning policy (grants
    hold ``learning_policy: null``; admins bypass grants entirely), so an
    explicit ask must route even though the composer capability stays
    "chat" — and the chat frontend's send shape (``capability: null``) must
    route as well."""
    import deeptutor.multi_user.learning_access as learning_access
    from deeptutor.runtime.capability_routing import route_explicit_visualize_request

    monkeypatch.setattr(
        learning_access,
        "current_learning_policy",
        lambda: None,
        raising=True,
    )
    route = route_explicit_visualize_request(
        "用可视化工具画一个二次函数 y=x²-2x-3 的图像", "chat"
    )
    assert route is not None and route.capability == "visualize"

    route = route_explicit_visualize_request(
        "用可视化工具画一个二次函数 y=x²-2x-3 的图像", None
    )
    assert route is not None and route.capability == "visualize"


async def _run_turn(runtime: TurnRuntimeManager, payload: dict) -> tuple[dict, dict]:
    session, turn = await runtime.start_turn(
        {
            "type": "start_turn",
            "tools": [],
            "knowledge_bases": [],
            "attachments": [],
            "language": "en",
            **payload,
        }
    )
    async for _event in runtime.subscribe_turn(turn["id"], after_seq=0):
        pass
    return session, turn


@pytest.mark.asyncio
async def test_a_one_turn_quiz_leaves_the_conversation_in_chat(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reading "Quiz me" runs one turn in the quiz engine. The next message
    is still chat, so the quiz settings card must not open under it."""
    captured: dict = {"global_enabled": False}
    _configure_runtime(monkeypatch, captured, tmp_path)
    runtime = TurnRuntimeManager(SQLiteSessionStore(tmp_path / "once.db"))

    session, _ = await _run_turn(
        runtime, {"content": "hello", "session_id": None, "capability": "chat", "config": {}}
    )
    _, turn = await _run_turn(
        runtime,
        {
            "content": "Quiz me on this page",
            "session_id": session["id"],
            "capability": "deep_question",
            "capability_once": True,
            "config": {"mode": "custom", "num_questions": 3},
        },
    )

    assert turn["capability"] == "deep_question"
    assert captured["active_capability"] == "deep_question"
    detail = await runtime.store.get_session(session["id"])
    assert detail is not None
    assert detail["preferences"]["capability"] == "chat"
    messages = await runtime.store.get_messages(session["id"])
    quiz_request = [row for row in messages if row["role"] == "user"][-1]
    # Recorded, so a regenerate runs as a quiz once again — and only once.
    assert quiz_request["metadata"]["request_snapshot"]["capabilityOnce"] is True

    _, again = await runtime.regenerate_last_turn(session["id"])
    async for _event in runtime.subscribe_turn(again["id"], after_seq=0):
        pass
    assert again["capability"] == "deep_question"
    detail = await runtime.store.get_session(session["id"])
    assert detail is not None
    assert detail["preferences"]["capability"] == "chat"


@pytest.mark.asyncio
async def test_choosing_quiz_mode_still_makes_it_the_conversations_mode(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {"global_enabled": False}
    _configure_runtime(monkeypatch, captured, tmp_path)
    runtime = TurnRuntimeManager(SQLiteSessionStore(tmp_path / "mode.db"))

    session, _ = await _run_turn(
        runtime,
        {
            "content": "Quiz me",
            "session_id": None,
            "capability": "deep_question",
            "config": {"mode": "custom", "num_questions": 3},
        },
    )

    detail = await runtime.store.get_session(session["id"])
    assert detail is not None
    assert detail["preferences"]["capability"] == "deep_question"
