"""API tests for the admin user-management additions (K12 role rollout).

Covers ``PUT /users/{username}/children`` (family linkage: parent-only,
live-student validation, self-linking rejected) and ``PUT /users/{username}/password``
(admin reset that preserves role and the parent ``children`` list).
"""

from __future__ import annotations

import json
from pathlib import Path

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
    from deeptutor.api.routers import auth as auth_router
    from deeptutor.multi_user import identity, paths as mu_paths
    from deeptutor.services.auth import TokenPayload


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    users_root = tmp_path / "data" / "users"
    system_root = tmp_path / "data" / "system"
    users_file = system_root / "auth" / "users.json"

    monkeypatch.setattr(mu_paths, "USERS_ROOT", users_root)
    monkeypatch.setattr(mu_paths, "SYSTEM_ROOT", system_root)
    monkeypatch.setattr(mu_paths, "_path_services", {})
    monkeypatch.setattr(identity, "USERS_FILE", users_file)
    monkeypatch.setattr(
        identity,
        "LEGACY_USERS_FILE",
        tmp_path / "data" / "user" / "auth_users.json",
    )
    monkeypatch.setattr(auth_router, "POCKETBASE_ENABLED", False)
    return {"users_file": users_file}


def _client(username: str = "admin", role: str = "admin") -> TestClient:
    app = FastAPI()
    app.include_router(auth_router.router)
    app.dependency_overrides[auth_router.require_admin] = lambda: TokenPayload(
        username=username, role=role, user_id=f"u_{username}"
    )
    return TestClient(app)


def _user(uid: str, role: str) -> dict:
    return {
        "id": uid,
        "hash": "$2b$12$fake-hash-for-tests",
        "role": role,
        "created_at": "2026-01-01T00:00:00+00:00",
        "disabled": False,
        "avatar": "",
    }


def _write_users(users_file: Path, users: dict) -> None:
    users_file.parent.mkdir(parents=True, exist_ok=True)
    users_file.write_text(json.dumps(users, ensure_ascii=False), encoding="utf-8")


def _seed(isolated: dict[str, Path]) -> None:
    _write_users(
        isolated["users_file"],
        {
            "admin": _user("u_admin", "admin"),
            "p1": _user("u_p1", "parent"),
            "s1": _user("u_s1", "student"),
            "s2": _user("u_s2", "student"),
            "t1": _user("u_t1", "teacher"),
        },
    )


def test_children_endpoint_validates_roles(isolated: dict[str, Path]) -> None:
    _seed(isolated)
    client = _client()

    # Non-parent target is rejected.
    response = client.put(
        "/users/t1/children", json={"children": ["s1"]}
    )
    assert response.status_code == 400
    assert "parent" in response.json()["detail"]

    # A child name that is not a live student account is rejected.
    response = client.put("/users/p1/children", json={"children": ["ghost"]})
    assert response.status_code == 400
    assert "ghost" in response.json()["detail"]

    # A parent cannot be their own child.
    response = client.put("/users/p1/children", json={"children": ["p1"]})
    assert response.status_code == 400

    # Valid roster persists; a follow-up read shows the linkage.
    response = client.put("/users/p1/children", json={"children": ["s1", "s2"]})
    assert response.status_code == 200
    assert response.json()["children"] == ["s1", "s2"]
    record = identity.load_users()["p1"]
    assert record["children"] == ["s1", "s2"]


def test_password_reset_preserves_role_and_children(
    isolated: dict[str, Path],
) -> None:
    users_file = isolated["users_file"]
    parent = _user("u_p1", "parent")
    parent["children"] = ["s1"]
    _write_users(users_file, {"admin": _user("u_admin", "admin"), "p1": parent, "s1": _user("u_s1", "student")})
    client = _client()

    response = client.put("/users/p1/password", json={"password": "new-pass-123"})
    assert response.status_code == 200
    record = identity.load_users()["p1"]
    assert record["role"] == "parent"
    assert record["children"] == ["s1"]
    assert record["hash"] != "$2b$12$fake-hash-for-tests"

    # Unknown user is 404.
    assert (
        client.put("/users/ghost/password", json={"password": "new-pass-123"}).status_code
        == 404
    )


def test_password_reset_rejects_short(isolated: dict[str, Path]) -> None:
    _seed(isolated)
    response = _client().put("/users/s1/password", json={"password": "short"})
    assert response.status_code == 422
