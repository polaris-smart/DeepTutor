"""API tests: parent-scoped learning insights (``GET /class-insights/my-children``).

A parent sees mastery aggregation only for the student accounts listed in the
parent record's ``children`` field; students and other parents are rejected.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

try:
    from fastapi import FastAPI, Depends
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover
    FastAPI = None
    TestClient = None

pytestmark = pytest.mark.skipif(
    FastAPI is None or TestClient is None, reason="fastapi not installed"
)

if FastAPI is not None and TestClient is not None:
    from deeptutor.api.routers import auth as auth_module
    from deeptutor.api.routers.auth import require_auth
    from deeptutor.api.routers.class_insights import router as insights_router
    from deeptutor.multi_user import identity, paths as mu_paths
    from deeptutor.services.auth import TokenPayload


@pytest.fixture
def family_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    users_root = tmp_path / "data" / "users"
    system_root = tmp_path / "data" / "system"
    users_file = system_root / "auth" / "users.json"
    monkeypatch.setattr(mu_paths, "USERS_ROOT", users_root)
    monkeypatch.setattr(mu_paths, "SYSTEM_ROOT", system_root)
    monkeypatch.setattr(mu_paths, "_path_services", {})
    monkeypatch.setattr(identity, "USERS_FILE", users_file)
    monkeypatch.setattr(
        identity, "LEGACY_USERS_FILE", tmp_path / "data" / "user" / "auth_users.json"
    )
    users_file.parent.mkdir(parents=True, exist_ok=True)
    users_file.write_text(json.dumps({
        "p1": {"id": "u_p1", "hash": "x", "role": "parent", "children": ["kid_a", "kid_b", "kid_c"]},
        "kid_a": {"id": "u_a", "hash": "x", "role": "student"},
        "kid_b": {"id": "u_b", "hash": "x", "role": "student"},
        "other": {"id": "u_o", "hash": "x", "role": "student"},
    }), encoding="utf-8")
    return users_file


def _client(role: str, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = FastAPI()
    app.include_router(insights_router, prefix="/api/v1/class-insights")
    monkeypatch.setattr(auth_module, "AUTH_ENABLED", True)
    app.dependency_overrides[require_auth] = lambda: TokenPayload(
        username="p1" if role == "parent" else f"u_{role}", role=role, user_id=f"u_{role}"
    )
    return TestClient(app)


def test_parent_sees_only_own_children(family_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 喂 kid_a 学习数据；kid_b 无数据（fail-open 跳过）；other 不在 children 名单
    from deeptutor.multi_user import paths as mu_paths_x

    learning_dir = mu_paths.USERS_ROOT / "u_a" / "user" / "workspace" / "learning"
    learning_dir.mkdir(parents=True, exist_ok=True)
    progress = {
        "book_id": "bk_1", "book_title": "数学", "updated_at": 1700000000.0,
        "modules": [{"id": "m1", "name": "m", "order": 1, "knowledge_points": [
            {"id": "kp1", "module_id": "m1", "name": "集合", "type": "concept",
             "interactions": 3, "correct": 3, "partial": 0, "wrong": 0,
             "last_seen_at": 1700000000.0, "mastery": 0.9}]}],
    }
    (learning_dir / "bk_1.json").write_text(json.dumps(progress), encoding="utf-8")

    client = _client("parent", monkeypatch)
    response = client.get("/api/v1/class-insights/my-children")
    assert response.status_code == 200
    students = response.json()["students"]
    names = [s["username"] for s in students]
    assert set(names) == {"kid_a", "kid_b"}  # own children only
    by_name = {s["username"]: s for s in students}
    assert by_name["kid_b"]["no_data"] is True  # 无学习数据的孩子以占位出现
    assert by_name["kid_a"]["kp_total"] >= 1


def test_student_cannot_use_parent_insights(family_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client("student", monkeypatch)
    response = client.get("/api/v1/class-insights/my-children")
    assert response.status_code == 403
