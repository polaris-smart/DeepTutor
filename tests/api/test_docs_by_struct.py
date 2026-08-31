"""API tests for ``GET /knowledge/{kb}/docs/by-struct``.

The canonicalization orchestrator reads a chapter's verbatim prose through
this endpoint, so ``full_text=true`` must return the node's whole content —
the default 200-char ``preview`` is a browser affordance, not a content
channel. Docstore is faked with ``SimpleNamespace`` nodes exactly like
``tests/api/test_exam_paper.py``; no index or network is needed.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
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
    with patch("deeptutor.services.config.load_config_with_main", return_value={}):
        knowledge_module = importlib.import_module("deeptutor.api.routers.knowledge")
else:  # pragma: no cover - guarded by pytestmark
    knowledge_module = None


class _Node:
    def __init__(self, node_id: str, *, metadata: dict, text: str = "") -> None:
        self.node_id = node_id
        self.metadata = metadata
        self.text = text

    def get_content(self) -> str:
        return self.text


def _client(monkeypatch: pytest.MonkeyPatch, *nodes: _Node) -> TestClient:
    app = FastAPI()
    app.include_router(knowledge_module.router, prefix="/api/v1/knowledge")
    docstore = SimpleNamespace(docs={n.node_id: n for n in nodes})
    monkeypatch.setattr(
        knowledge_module, "_load_kb_docstore", lambda kb_name: (kb_name, docstore)
    )
    return TestClient(app)


_LONG_BODY = "教材原文。" * 200  # 1000 chars — well past the preview cap


def _long_node() -> _Node:
    return _Node(
        "n1",
        metadata={"struct_path": "第一课/第一框", "file_name": "政必修1.pdf"},
        text=_LONG_BODY,
    )


def test_by_struct_previews_are_capped_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, _long_node())
    response = client.get(
        "/api/v1/knowledge/政治/docs/by-struct", params={"path": "第一课"}
    )
    assert response.status_code == 200
    node = response.json()["nodes"][0]
    assert len(node["preview"]) == 200
    assert "text" not in node


def test_by_struct_full_text_returns_the_whole_node(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, _long_node())
    response = client.get(
        "/api/v1/knowledge/政治/docs/by-struct",
        params={"path": "第一课", "full_text": "true"},
    )
    assert response.status_code == 200
    node = response.json()["nodes"][0]
    assert node["text"] == _LONG_BODY
    # The preview stays alongside so existing readers are unaffected.
    assert node["preview"] == _LONG_BODY[:200]


def test_by_struct_full_text_keeps_struct_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, _long_node())
    response = client.get(
        "/api/v1/knowledge/政治/docs/by-struct",
        params={"path": "第一课", "full_text": "true"},
    )
    node = response.json()["nodes"][0]
    assert node["struct_path"] == "第一课/第一框"
    assert node["file_name"] == "政必修1.pdf"
    assert node["node_id"] == "n1"


def test_by_struct_full_text_respects_the_path_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(
        monkeypatch,
        _long_node(),
        _Node("n2", metadata={"struct_path": "第二课/第一框"}, text="别的课"),
    )
    response = client.get(
        "/api/v1/knowledge/政治/docs/by-struct",
        params={"path": "第一课", "full_text": "true"},
    )
    nodes = response.json()["nodes"]
    assert [n["node_id"] for n in nodes] == ["n1"]
