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
    from fastapi import FastAPI
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
        "/api/v1/knowledge/数学/config", json={"rag_provider": "llamaindex"}
    )
    assert response.status_code == 403


def test_teacher_can_write_kb_config(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client("teacher", monkeypatch)
    response = client.put(
        "/api/v1/knowledge/数学/config", json={"rag_provider": "llamaindex"}
    )
    assert response.status_code == 200


def test_admin_can_write_kb_config(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client("admin", monkeypatch)
    response = client.put(
        "/api/v1/knowledge/数学/config", json={"rag_provider": "llamaindex"}
    )
    assert response.status_code == 200
