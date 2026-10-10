"""Tests for the Bilibili provider in immersive watching (video_learning).

Covers: URL parsing (BV / multi-page / b23.tv / illegal), settings validation
with the third provider, resolve_material URL routing, and the duck-typed
video_id that lets the shared pipeline key bilibili materials uniformly.
"""

from __future__ import annotations

import pytest

from deeptutor.video_learning import service as vls
from deeptutor.video_learning.service import (
    TimedMediaError,
    parse_bilibili_url,
    parse_youtube_url,
)


def test_parse_bilibili_url_canonical_form() -> None:
    r = parse_bilibili_url("https://www.bilibili.com/video/BV1GJ411x7h7?p=2")
    assert r.bvid == "BV1GJ411x7h7"
    assert r.page == 2
    assert r.canonical_url.endswith("/video/BV1GJ411x7h7?p=2")


def test_parse_bilibili_url_accepts_shortener_and_rejects_foreign_hosts() -> None:
    assert parse_bilibili_url("https://b23.tv/BV1GJ411x7h7").bvid == "BV1GJ411x7h7"
    with pytest.raises(TimedMediaError):
        parse_bilibili_url("https://youtube.com/watch?v=dQw4w9WgXcQ")


def test_settings_accept_bilibili_provider() -> None:
    settings = vls_normalize({"default_provider": "bilibili"})
    assert settings["default_provider"] == "bilibili"
    assert "bilibili" in settings


def vls_normalize(payload):
    return vls_module().normalize_video_learning_settings(payload)


def vls_module():
    import deeptutor.video_learning.service as m

    return m


def test_resolve_material_routes_bilibili_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    calls = {}

    async def fake_view(bvid, page):
        calls["bvid"] = bvid
        return {"title": "测试视频", "duration_seconds": 300, "cover": "", "page_cid": 1, "page_part": ""}

    monkeypatch.setattr(vls_module(), "_bilibili_view", fake_view)
    monkeypatch.setattr(
        vls_module(), "load_video_learning_settings", lambda: {
            "version": 1,
            "default_provider": "youtube",
            "youtube": {"transcript_provider": "none"},
            "invidious": {"api_base_url": "", "public_base_url": ""},
            "bilibili": {"cc_cookie": ""},
        }
    )
    from pathlib import Path
    import tempfile

    monkeypatch.chdir(tempfile.mkdtemp())

    async def run():
        return await vls_module().resolve_material(
            "https://www.bilibili.com/video/BV1GJ411x7h7", language="zh"
        )

    material = asyncio.run(run())
    assert calls["bvid"] == "BV1GJ411x7h7"
    assert material["metadata"]["title"] == "测试视频"
    # v1 诚实降级：B 站 CC 字幕未接，cues 为空且 source 标明
    assert material["transcript"]["source"] == "bilibili_cc_unavailable"


def test_bilibili_request_duck_types_video_id() -> None:
    r = parse_bilibili_url("https://www.bilibili.com/video/BV1GJ411x7h7?p=3")
    assert r.video_id == "BV1GJ411x7h7-p3"  # shared pipeline keying


def test_bilibili_view_retries_once_on_412_then_raises_retryable_upstream_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N68: exactly one delayed retry on 412, then a retryable upstream error."""
    import asyncio

    from deeptutor.video_learning.service import TimedMediaUpstreamError

    calls = {"count": 0}

    class FakeResponse:
        status_code = 412

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, *args, **kwargs):
            calls["count"] += 1
            return FakeResponse()

    monkeypatch.setattr(vls_module().httpx, "AsyncClient", FakeClient)
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(vls_module().asyncio, "sleep", fake_sleep)

    with pytest.raises(TimedMediaUpstreamError) as excinfo:
        asyncio.run(vls_module()._bilibili_view("BV1GJ411x7h7", 1))
    assert calls["count"] == 2  # original attempt + exactly one retry
    assert sleeps == [vls_module().BILIBILI_RATE_LIMIT_RETRY_DELAY_SECONDS]
    assert excinfo.value.upstream_status == 412
    assert "retry later" in str(excinfo.value)


def test_resolve_endpoint_maps_persistent_412_to_retryable_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N68: persistent 412 answers 502 {detail, upstream}, not a bare 400."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from deeptutor.api.routers import video_learning as video_router
    from deeptutor.video_learning.service import TimedMediaUpstreamError

    async def fake_resolve(*args, **kwargs):
        raise TimedMediaUpstreamError("Bilibili temporary rate-limit (412), retry later", 412)

    monkeypatch.setattr(video_router, "resolve_material", fake_resolve)
    app = FastAPI()
    app.include_router(video_router.router, prefix="/api/video-learning")
    with TestClient(app) as client:
        response = client.post(
            "/api/video-learning/materials/resolve",
            json={
                "url": "https://www.bilibili.com/video/BV1GJ411x7h7",
                "provider_override": "bilibili",
            },
        )

    assert response.status_code == 502
    assert response.json()["detail"] == {
        "detail": "Bilibili temporary rate-limit (412), retry later",
        "upstream": 412,
    }
