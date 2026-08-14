from __future__ import annotations

import importlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover - optional dependency in lightweight envs
    FastAPI = None
    TestClient = None

pytestmark = pytest.mark.skipif(
    FastAPI is None or TestClient is None, reason="fastapi not installed"
)

if FastAPI is not None and TestClient is not None:
    config_module = importlib.import_module("deeptutor.services.config")
    with patch.object(config_module, "load_config_with_main", return_value={}):
        knowledge_module = importlib.import_module("deeptutor.api.routers.knowledge")
else:  # pragma: no cover - guarded by pytestmark
    knowledge_module = None


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(knowledge_module.router, prefix="/api/v1/knowledge")
    return app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    index_path = tmp_path / "数学" / "video_index.json"
    index_path.parent.mkdir(parents=True)
    index_path.write_text(
        json.dumps(
            {
                "version": 1,
                "videos": [
                    {
                        "path": "一数/高考最后十课/三角函数.mp4",
                        "title": "三角函数与正弦定理",
                        "channel": "一数",
                        "collection": "高考最后十课",
                        "duration_s": 457,
                        "subject_hint": "数学",
                        "kp_hint": "三角函数、正弦定理",
                    },
                    {
                        "path": "一数/基础/函数.mp4",
                        "title": "函数基础",
                        "channel": "一数",
                        "collection": "基础",
                        "duration_s": None,
                        "subject_hint": "数学",
                        "kp_hint": "函数",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    from fastapi import HTTPException

    monkeypatch.setattr(
        knowledge_module,
        "_readable_kb",
        lambda _kb_name: (_ for _ in ()).throw(HTTPException(status_code=404)),
    )
    monkeypatch.setattr(knowledge_module, "current_kb_base_dir", lambda: tmp_path)
    return TestClient(_build_app())


def test_videos_api_matches_query_and_returns_public_fields(client: TestClient) -> None:
    response = client.get("/api/v1/knowledge/数学/videos", params={"query": "三角函数"})

    assert response.status_code == 200
    assert response.json() == [
        {
            "path": "一数/高考最后十课/三角函数.mp4",
            "title": "三角函数与正弦定理",
            "channel": "一数",
            "collection": "高考最后十课",
            "duration_s": 457,
            "score": 3,
        }
    ]


def test_videos_api_requires_two_shared_struct_path_keywords(client: TestClient) -> None:
    response = client.get(
        "/api/v1/knowledge/数学/videos",
        params={"struct_path": "必修二/三角函数/正弦定理"},
    )

    assert response.status_code == 200
    assert [item["path"] for item in response.json()] == ["一数/高考最后十课/三角函数.mp4"]
