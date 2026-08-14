from __future__ import annotations

import importlib
import json
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
    config_module = importlib.import_module("deeptutor.services.config")
    with patch.object(config_module, "load_config_with_main", return_value={}):
        knowledge_module = importlib.import_module("deeptutor.api.routers.knowledge")
else:  # pragma: no cover - guarded by pytestmark
    knowledge_module = None


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(knowledge_module.router, prefix="/api/v1/knowledge")
    return app


class _Node:
    def __init__(
        self,
        node_id: str,
        *,
        metadata: dict,
        text: str = "",
        ref_doc_id: str | None = None,
    ) -> None:
        self.node_id = node_id
        self.metadata = metadata
        self.text = text
        self.ref_doc_id = ref_doc_id

    def get_content(self) -> str:
        return self.text


def _docstore(*nodes: _Node):
    return SimpleNamespace(docs={node.node_id: node for node in nodes})


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    tree_a = {"title": "中国特色社会主义", "children": [{"title": "第一单元"}]}
    tree_b = {"title": "经济与社会", "children": [{"title": "第二单元"}]}
    docstore = _docstore(
        _Node(
            "node-a1",
            ref_doc_id="doc-a",
            text="第一课 " + "社会主义从空想到科学、从理论到实践的发展" * 20,
            metadata={
                "file_name": "必修1 中国特色社会主义.pdf",
                "doc_subject": "政治",
                "doc_type": "textbook",
                "doc_grade": "",
                "doc_tree": json.dumps(tree_a, ensure_ascii=False),
                "struct_path": "第一单元/第一课",
            },
        ),
        # A second chunk from the same source document must not duplicate its tree.
        _Node(
            "node-a2",
            ref_doc_id="doc-a",
            text="第二目 科学社会主义的理论与实践",
            metadata={
                "file_name": "必修1 中国特色社会主义.pdf",
                "doc_subject": "政治",
                "doc_type": "textbook",
                "doc_tree": json.dumps(tree_a, ensure_ascii=False),
                "struct_path": "第一单元/第一课/第二目",
            },
        ),
        _Node(
            "node-b",
            ref_doc_id="doc-b",
            text="收入分配与社会公平",
            metadata={
                "file_name": "必修2 经济与社会.pdf",
                "doc_subject": "政治",
                "doc_type": "textbook",
                "doc_grade": "高一",
                "doc_tree": tree_b,
                "struct_path": "第二单元/第四课",
            },
        ),
        _Node(
            "node-c",
            ref_doc_id="doc-c",
            text="无结构的讲义",
            metadata={"file_name": "讲义.pdf", "doc_type": "lecture"},
        ),
    )
    monkeypatch.setattr(
        knowledge_module,
        "_load_kb_docstore",
        lambda kb_name: (kb_name, docstore),
    )
    return TestClient(_build_app())


def test_textbook_tree_aggregates_filters_and_deduplicates(client: TestClient) -> None:
    response = client.get("/api/v1/knowledge/政治/textbook-tree")

    assert response.status_code == 200
    payload = response.json()
    assert payload["kb_name"] == "政治"
    assert [book["doc_id"] for book in payload["textbooks"]] == ["doc-a", "doc-b"]
    assert payload["textbooks"][0] == {
        "doc_id": "doc-a",
        "file_name": "必修1 中国特色社会主义.pdf",
        "subject": "政治",
        "doc_type": "textbook",
        "grade": "",
        "tree": {"title": "中国特色社会主义", "children": [{"title": "第一单元"}]},
    }
    assert payload["textbooks"][1]["grade"] == "高一"


def test_textbook_tree_returns_empty_array_for_empty_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        knowledge_module,
        "_load_kb_docstore",
        lambda kb_name: (kb_name, None),
    )

    with TestClient(_build_app()) as test_client:
        response = test_client.get("/api/v1/knowledge/空库/textbook-tree")

    assert response.status_code == 200
    assert response.json() == {"kb_name": "空库", "textbooks": []}


def test_textbook_tree_uses_existing_kb_read_access_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    monkeypatch.setattr(knowledge_module, "_overridden_kb_manager", lambda: None)
    monkeypatch.setattr(
        knowledge_module,
        "resolve_kb",
        lambda _kb_name: (_ for _ in ()).throw(
            HTTPException(status_code=403, detail="Knowledge base is not assigned to you")
        ),
    )

    with TestClient(_build_app()) as test_client:
        response = test_client.get("/api/v1/knowledge/政治/textbook-tree")

    assert response.status_code == 403
    assert response.json()["detail"] == "Knowledge base is not assigned to you"


def test_docs_by_struct_prefix_matches_limits_and_truncates_preview(
    client: TestClient,
) -> None:
    response = client.get(
        "/api/v1/knowledge/政治/docs/by-struct",
        params={"path": "第一单元/第一课", "limit": 1},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["path"] == "第一单元/第一课"
    assert len(payload["nodes"]) == 1
    assert payload["nodes"][0]["node_id"] == "node-a1"
    assert payload["nodes"][0]["struct_path"] == "第一单元/第一课"
    assert payload["nodes"][0]["file_name"] == "必修1 中国特色社会主义.pdf"
    assert payload["nodes"][0]["preview"].startswith("第一课 社会主义")
    assert len(payload["nodes"][0]["preview"]) == 200


@pytest.mark.parametrize("params", [{}, {"path": ""}])
def test_docs_by_struct_requires_nonempty_path(client: TestClient, params: dict) -> None:
    response = client.get("/api/v1/knowledge/政治/docs/by-struct", params=params)

    assert response.status_code == 422
