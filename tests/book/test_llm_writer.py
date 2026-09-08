from __future__ import annotations

import pytest

from deeptutor.book.blocks import _llm_writer


@pytest.mark.asyncio
async def test_llm_json_normalizes_array_to_expected_key(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_llm_text(**_: object) -> str:
        return '[{"front": "A", "back": "B"}]'

    monkeypatch.setattr(_llm_writer, "llm_text", fake_llm_text)

    data = await _llm_writer.llm_json(
        user_prompt="cards",
        system_prompt="system",
        expected_key="cards",
    )

    assert data == {"cards": [{"front": "A", "back": "B"}]}


@pytest.mark.asyncio
async def test_llm_json_uses_single_object_from_array(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_llm_text(**_: object) -> str:
        return '[{"code": "print(1)", "language": "python"}]'

    monkeypatch.setattr(_llm_writer, "llm_text", fake_llm_text)

    data = await _llm_writer.llm_json(user_prompt="code", system_prompt="system")

    assert data["code"] == "print(1)"
    assert data["language"] == "python"


@pytest.mark.asyncio
async def test_llm_json_retries_structured_calls_with_low_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The retry uses "low" reasoning effort rather than "minimal": vLLM-served
    # local models (e.g. Qwen) reject "minimal" and only accept low/medium/high.
    calls: list[str | None] = []

    async def fake_llm_text(**kwargs: object) -> str:
        effort = kwargs.get("reasoning_effort")
        calls.append(effort if isinstance(effort, str) else None)
        if effort == "low":
            return '{"events": [{"date": "2026", "title": "Ready"}]}'
        return ""

    monkeypatch.setattr(_llm_writer, "llm_text", fake_llm_text)

    data = await _llm_writer.llm_json(
        user_prompt="timeline",
        system_prompt="system",
        expected_key="events",
    )

    assert calls == [None, "low"]
    assert data["events"][0]["title"] == "Ready"
    assert data["_metadata"]["reasoning_retry"] == "low"


# ─────────────────────────────────────────────────────────────────────────────
# DEEPTUTOR_BOOKGEN_PROFILE 路由固化（P1-E D1）：块生成走指定 profile
# ─────────────────────────────────────────────────────────────────────────────


def _catalog_with_profiles() -> dict:
    return {
        "services": {
            "llm": {
                "active_profile_id": "prof-weak",
                "active_model_id": "model-weak-1",
                "profiles": [
                    {
                        "id": "prof-weak",
                        "binding": "openai_compat_test",
                        "models": [{"id": "model-weak-1", "model": "glmfree-air"}],
                    },
                    {
                        "id": "prof-strong",
                        "binding": "openai_compat_test",
                        "active_model_id": "model-strong-2",
                        "models": [
                            {"id": "model-strong-1", "model": "doubao-seed-other"},
                            {"id": "model-strong-2", "model": "doubao-seed-1-6"},
                        ],
                    },
                ],
            }
        }
    }


def _resolved_for_selection(selection: dict | None):
    from deeptutor.services.config.provider_runtime import ResolvedLLMConfig

    if selection is None:
        model = "glmfree-air"
    elif selection["profile_id"] == "prof-strong" and selection["model_id"] == "model-strong-2":
        model = "doubao-seed-1-6"
    else:
        raise AssertionError(f"unexpected selection payload: {selection}")
    return ResolvedLLMConfig(
        model=model,
        provider_name="openai_compat_test",
        provider_mode="standard",
        binding_hint="openai_compat_test",
        binding="openai_compat_test",
        api_key="sk-test",
        base_url="https://example.com/v1",
        effective_url="https://example.com/v1",
        api_version=None,
        extra_headers={},
        reasoning_effort=None,
        context_window=None,
    )


def _patch_catalog_and_env(monkeypatch: pytest.MonkeyPatch, *, env: str | None) -> None:
    from deeptutor.services.config import model_catalog as mc_module
    from deeptutor.services.llm import config as config_module

    class _FakeService:
        def load(self):
            return _catalog_with_profiles()

    monkeypatch.setattr(mc_module, "get_model_catalog_service", lambda: _FakeService())
    if env is None:
        monkeypatch.delenv("DEEPTUTOR_BOOKGEN_PROFILE", raising=False)
    else:
        monkeypatch.setenv("DEEPTUTOR_BOOKGEN_PROFILE", env)
    monkeypatch.setattr(
        config_module,
        "resolve_llm_runtime_config",
        lambda *args, **kwargs: _resolved_for_selection(kwargs.get("llm_selection")),
    )
    monkeypatch.setattr(config_module, "_LLM_CONFIG_CACHE", None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "env,expected_model",
    [
        ("prof-strong", "doubao-seed-1-6"),  # 设 pin：块生成固定走强档 profile
        (None, "glmfree-air"),  # 未设 pin：回落 active，行为兼容
    ],
)
async def test_llm_text_routes_via_bookgen_profile_pin(
    monkeypatch: pytest.MonkeyPatch, env: str | None, expected_model: str
) -> None:
    """块生成唯一取配置 chokepoint（llm_text）按 pin 路由。"""
    _patch_catalog_and_env(monkeypatch, env=env)

    captured: dict = {}

    async def fake_complete(**kwargs: object) -> str:
        captured.update(kwargs)
        return "  ok  "

    monkeypatch.setattr(_llm_writer, "llm_complete", fake_complete)

    result = await _llm_writer.llm_text(user_prompt="u", system_prompt="s")

    assert result == "ok"
    assert captured["model"] == expected_model
    assert captured["api_key"] == "sk-test"
