"""B2-b【3】import-from-book 读 canonical KP 树缓存（有缓存/无缓存）."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

FastAPI = pytest.importorskip("fastapi").FastAPI
TestClient = pytest.importorskip("fastapi.testclient").TestClient

import deeptutor.book.storage as storage_module
from deeptutor.api.routers import mastery_path as mastery_router_module
from deeptutor.api.routers.auth import require_auth
from deeptutor.book.models import Book
from deeptutor.learning.service import LearningService
from deeptutor.learning.storage import LearningStore
from deeptutor.services.auth import TokenPayload
from deeptutor.services.path_service import PathService

CANONICAL_TREE = {
    "title": "人教A数学选择性必修第三册",
    "children": [
        {
            "title": "第六章 计数原理",
            "level": 1,
            "struct_path": "第六章 计数原理",
            "node_id": "ch6",
            "children": [
                {
                    "title": "6.2 排列与组合",
                    "level": 3,
                    "struct_path": "第六章 计数原理/6.2 排列与组合",
                    "node_id": "62",
                    "children": [
                        {
                            "title": "6.2.1 排列",
                            "level": 4,
                            "struct_path": "第六章 计数原理/6.2 排列与组合/6.2.1 排列",
                            "node_id": "621",
                            "children": [],
                            "type": "mu",
                        },
                        {
                            "title": "6.2.2 组合",
                            "level": 4,
                            "struct_path": "第六章 计数原理/6.2 排列与组合/6.2.2 组合",
                            "node_id": "622",
                            "children": [],
                            "type": "mu",
                        },
                    ],
                },
                # 纯结构节点，无目级 KP → 不成 module。
                {
                    "title": "6.3 二项式定理",
                    "level": 3,
                    "struct_path": "第六章 计数原理/6.3 二项式定理",
                    "node_id": "63",
                    "children": [],
                },
            ],
        }
    ],
}


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    learning_dir = tmp_path / "data" / "learning"
    service = PathService(workspace_root=tmp_path / "data")
    storage_module._storages.clear()
    monkeypatch.setattr(storage_module, "get_path_service", lambda: service)
    book_storage = storage_module.get_book_storage()
    monkeypatch.setattr(mastery_router_module, "get_book_storage", lambda: book_storage)
    monkeypatch.setattr(
        mastery_router_module,
        "_cancel_active_learning_turn",
        lambda book_id: asyncio.sleep(0),
    )
    original_init = LearningStore.__init__
    monkeypatch.setattr(
        LearningStore,
        "__init__",
        lambda self, root=None: original_init(self, root=learning_dir / "mastery"),
    )
    monkeypatch.setattr(
        mastery_router_module,
        "get_learning_service",
        lambda: LearningService(store=LearningStore(root=learning_dir / "mastery")),
    )

    app = FastAPI()
    app.include_router(mastery_router_module.router, prefix="/api/v1/learning")
    app.dependency_overrides[require_auth] = lambda: TokenPayload(
        username="alice", role="student", user_id="u_alice"
    )
    return {"client": TestClient(app), "book_storage": book_storage, "store": learning_dir}


def test_import_from_book_prefers_cached_canonical_tree(isolated: dict) -> None:
    isolated["book_storage"].save_book(Book(id="bk_canon", title="数学选必三"))
    assert (
        isolated["book_storage"].save_canonical_kp_tree("bk_canon", CANONICAL_TREE) is True
    )

    response = isolated["client"].post(
        "/api/v1/learning/progress/bk_canon/import-from-book", json={"chapters": []}
    )
    assert response.status_code == 200, response.text
    assert response.json()["module_count"] == 1

    store = LearningStore(root=isolated["store"] / "mastery")
    progress = store.load("bk_canon")
    assert progress is not None
    assert [m.name for m in progress.modules] == ["6.2 排列与组合"]
    kps = progress.modules[0].knowledge_points
    assert [kp.name for kp in kps] == ["6.2.1 排列", "6.2.2 组合"]
    assert all(kp.type.value == "concept" for kp in kps)
    # 教材桥：目级 node_id / struct_path 直达教材树。
    assert kps[0].textbook_node_id == "621"
    assert kps[0].struct_path == "第六章 计数原理/6.2 排列与组合/6.2.1 排列"
    assert kps[1].textbook_node_id == "622"


def test_import_from_book_falls_back_without_cache(isolated: dict) -> None:
    response = isolated["client"].post(
        "/api/v1/learning/progress/bk_mech/import-from-book",
        json={
            "chapters": [
                {
                    "title": "第一章 集合",
                    "knowledge_points": ["集合的概念", "集合间的基本关系"],
                    "struct_path": "第一章 集合",
                    "textbook_node_id": "ch1",
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["module_count"] == 1

    store = LearningStore(root=isolated["store"] / "mastery")
    progress = store.load("bk_mech")
    assert progress is not None
    module = progress.modules[0]
    assert module.name == "第一章 集合"
    assert [kp.name for kp in module.knowledge_points] == [
        "集合的概念",
        "集合间的基本关系",
    ]
    # 机械态：整章共用一个桥（旧行为保持）。
    assert all(kp.textbook_node_id == "ch1" for kp in module.knowledge_points)
