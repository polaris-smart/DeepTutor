"""API tests: KB write endpoints require admin/teacher (K12 student read-only).

Background: xueban A7 实锤 — ``PUT /{kb}/config`` was writable by any logged-in
account (a student could flip the RAG provider of a shared KB). The five KB
write endpoints now sit behind ``require_admin_or_teacher``; authorization is
verified at the dependency, so the tests override it per role.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

try:
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover
    FastAPI = None
    TestClient = None

pytestmark = pytest.mark.skipif(
    FastAPI is None or TestClient is None, reason="fastapi not installed"
)

if FastAPI is not None and TestClient is not None:
    from deeptutor.api.routers import knowledge as knowledge_module
    from deeptutor.api.routers.auth import require_auth
    from deeptutor.services.auth import TokenPayload


def _client(role: str, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = FastAPI()
    app.include_router(knowledge_module.router, prefix="/api/v1/knowledge")
    # Route through the real require_admin_or_teacher: force AUTH_ENABLED on and
    # stub only the token source (require_auth) with the caller's role.
    from deeptutor.api.routers import auth as auth_module

    monkeypatch.setattr(auth_module, "AUTH_ENABLED", True)
    app.dependency_overrides[require_auth] = lambda: TokenPayload(
        username=f"u_{role}", role=role, user_id=f"u_{role}"
    )
    # KB config read path hits the config service; stub the write path pieces.
    import deeptutor.services.config as config_service

    class _FakeConfigService:
        def get_kb_config(self, name):
            return {"rag_provider": "llamaindex"}

        def set_kb_config(self, name, config):
            return config

    monkeypatch.setattr(
        config_service, "get_kb_config_service", lambda: _FakeConfigService()
    )
    # has_ready_provider_index used on provider switch; keep it False.
    monkeypatch.setattr(
        "deeptutor.services.rag.index_probe.has_ready_provider_index",
        lambda kb_dir, provider: False,
    )
    return TestClient(app)


def test_student_cannot_write_kb_config(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client("student", monkeypatch)
    response = client.put(
        "/api/v1/knowledge/knowledge-bases/数学/config", json={"rag_provider": "llamaindex"}
    )
    assert response.status_code == 403


def test_teacher_can_write_kb_config(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client("teacher", monkeypatch)
    response = client.put(
        "/api/v1/knowledge/knowledge-bases/数学/config", json={"rag_provider": "llamaindex"}
    )
    assert response.status_code == 200


def test_admin_can_write_kb_config(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client("admin", monkeypatch)
    response = client.put(
        "/api/v1/knowledge/knowledge-bases/数学/config", json={"rag_provider": "llamaindex"}
    )
    assert response.status_code == 200


def test_parent_can_upload_family_materials(monkeypatch: pytest.MonkeyPatch) -> None:
    """家长资料直通（老板 09-01 拍板）：parent 上传进 KB，student 不行。"""
    from deeptutor.api.routers.auth import require_auth

    app = FastAPI()
    app.include_router(knowledge_module.router, prefix="/api/v1/knowledge")
    monkeypatch.setattr(auth_module := __import__(
        "deeptutor.api.routers.auth", fromlist=["AUTH_ENABLED"]), "AUTH_ENABLED", True)
    app.dependency_overrides[require_auth] = lambda: TokenPayload(
        username="u_parent", role="parent", user_id="u_parent"
    )
    # upload 的后续依赖按最小化 mock：_writable_kb 走 override 之外，直测门禁后
    # 的行为需完整 KB 环境，此处仅验证门禁本身放行 parent：
    from deeptutor.api.routers.knowledge import require_kb_writer

    app2 = FastAPI()
    app2.dependency_overrides[require_auth] = lambda: TokenPayload(
        username="u_parent", role="parent", user_id="u_parent"
    )

    @app2.post("/gate")
    async def gate(user: TokenPayload = Depends(require_kb_writer)):
        return {"role": user.role}

    ok = TestClient(app2).post("/gate")
    assert ok.status_code == 200 and ok.json()["role"] == "parent"

    app2.dependency_overrides[require_auth] = lambda: TokenPayload(
        username="u_student", role="student", user_id="u_student"
    )
    denied = TestClient(app2).post("/gate")
    assert denied.status_code == 403


def test_config_write_still_denies_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    """直通只限资料上传：parent 改 KB 配置仍 403（config=admin/teacher）。"""
    client = _client("parent", monkeypatch)
    response = client.put(
        "/api/v1/knowledge/knowledge-bases/数学/config", json={"rag_provider": "llamaindex"}
    )
    assert response.status_code == 403
