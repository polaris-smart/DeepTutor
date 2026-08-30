"""Tests for the Volcano Agent Plan TTS adapter (seed-tts-2.0)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from deeptutor.services.voice.adapters import get_tts_adapter
from deeptutor.services.voice.adapters.volc_plan import VolcPlanTTSAdapter
from deeptutor.services.voice.base import VoiceProviderError
from deeptutor.services.voice.config import TTSConfig

PLAN_URL = "https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional"


def _capture_post(monkeypatch: pytest.MonkeyPatch, response: httpx.Response) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def fake_post(self: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["headers"] = kwargs.get("headers")
        response.request = httpx.Request("POST", url)
        return response

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    return captured


def _config(**overrides: Any) -> TTSConfig:
    base = {
        "model": "seed-tts-2.0",
        "adapter": "volc_plan_tts",
        "api_key": "ark-plan-key",
        "base_url": PLAN_URL,
        "voice": "zh_female_demo",
        "response_format": "mp3",
    }
    base.update(overrides)
    return TTSConfig(**base)


def test_registry_exposes_volc_plan_tts() -> None:
    assert isinstance(get_tts_adapter("volc_plan_tts"), VolcPlanTTSAdapter)


@pytest.mark.anyio
async def test_posts_plan_url_with_api_key_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resp = httpx.Response(200, content=b"ID3-audio", headers={"content-type": "audio/mpeg"})
    captured = _capture_post(monkeypatch, resp)
    adapter = VolcPlanTTSAdapter()
    config = _config()

    audio, content_type = await adapter.synthesize("你好", config)

    assert audio == b"ID3-audio"
    assert content_type == "audio/mpeg"
    assert captured["url"] == PLAN_URL
    headers = captured["headers"]
    assert headers["X-Api-Key"] == "ark-plan-key"
    assert headers["X-Api-Resource-Id"] == "seed-tts-2.0"
    assert "X-Api-Request-Id" in headers
    params = captured["json"]["req_params"]
    assert params["text"] == "你好"
    assert params["speaker"] == "zh_female_demo"
    assert params["audio_params"]["format"] == "mp3"


@pytest.mark.anyio
async def test_refuses_non_plan_url(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_post(monkeypatch, httpx.Response(200, content=b"x"))
    adapter = VolcPlanTTSAdapter()
    config = _config(base_url="https://ark.cn-beijing.volces.com/api/v3")

    with pytest.raises(VoiceProviderError, match="plan"):
        await adapter.synthesize("你好", config)
    # 防误用计费线：拒绝时不得发出任何请求
    assert "url" not in captured


@pytest.mark.anyio
async def test_requires_speaker() -> None:
    adapter = VolcPlanTTSAdapter()
    config = _config(voice="")

    with pytest.raises(VoiceProviderError, match="speaker"):
        await adapter.synthesize("你好", config)


@pytest.mark.anyio
async def test_json_error_body_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import json as jsonlib

    error_body = jsonlib.dumps(
        {"header": {"code": 55000000, "message": "resource mismatch"}}
    )
    captured = _capture_post(
        monkeypatch,
        httpx.Response(200, content=error_body.encode(), headers={"content-type": "application/json"}),
    )
    adapter = VolcPlanTTSAdapter()

    with pytest.raises(VoiceProviderError, match="resource mismatch"):
        await adapter.synthesize("你好", config=_config())
    assert captured  # 请求已发出，错误来自响应体
