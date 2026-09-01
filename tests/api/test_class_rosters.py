"""Tests for the K12 class-roster endpoints and the class-filtered overview.

Covers: roster CRUD with live-student validation (unknown names rejected at
write, stale entries skipped at read), teacher/admin ownership boundaries,
whitelist semantics of the class-filtered overview (``no_data`` placeholder,
rostered members only), and the ``save_user`` hardening that keeps a
parent's ``children`` linkage across password resets.
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
    from deeptutor.api.routers.auth import require_admin_or_teacher
    from deeptutor.api.routers.class_insights import router as class_insights_router
    from deeptutor.learning.models import (
        KnowledgePoint,
        KnowledgeType,
        LearningModule,
        LearningProgress,
    )
    from deeptutor.multi_user import identity, paths as mu_paths
    from deeptutor.services.auth import TokenPayload


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Redirect the account store, roster document and workspaces under tmp_path."""
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
    return {"users_root": users_root, "users_file": users_file}


def _client(role: str = "teacher", username: str = "t1") -> TestClient:
    app = FastAPI()
    app.include_router(class_insights_router, prefix="/api/v1/class-insights")
    app.dependency_overrides[require_admin_or_teacher] = lambda: TokenPayload(
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


def _progress(book_id: str, kps: list[tuple[str, str, str, float]]) -> LearningProgress:
    module_id = f"{book_id}_m1"
    modules = [
        LearningModule(
            id=module_id,
            name="Module 1",
            order=0,
            knowledge_points=[
                KnowledgePoint(
                    id=kp_id, name=name, type=KnowledgeType(kp_type), module_id=module_id
                )
                for kp_id, name, kp_type, _ in kps
            ],
        )
    ]
    return LearningProgress(
        book_id=book_id,
        modules=modules,
        mastery_levels={kp_id: mastery for kp_id, _, _, mastery in kps},
        qualitative_mastery={},
        updated_at=1735689600.0,
    )


def _write_progress(
    users_root: Path, uid: str, book_id: str, progress: LearningProgress
) -> None:
    learning_dir = users_root / uid / "user" / "workspace" / "learning"
    learning_dir.mkdir(parents=True, exist_ok=True)
    (learning_dir / f"{book_id}.json").write_text(
        json.dumps(progress.model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )


def test_create_class_rejects_unknown_and_non_student(isolated: dict[str, Path]) -> None:
    _write_users(
        isolated["users_file"],
        {"t1": _user("u_t1", "teacher"), "alice": _user("u_alice", "student")},
    )
    client = _client(role="teacher", username="t1")

    # A name that resolves to no account is rejected outright — a silently
    # dropped typo would strand a real student outside the class view.
    response = client.post(
        "/api/v1/class-insights/classes",
        json={"name": "数学一班", "students": ["alice", "ghost"]},
    )
    assert response.status_code == 400
    assert "ghost" in response.json()["detail"]

    # A teacher account is not a valid roster member either.
    response = client.post(
        "/api/v1/class-insights/classes",
        json={"name": "数学一班", "students": ["t1"]},
    )
    assert response.status_code == 400

    response = client.post(
        "/api/v1/class-insights/classes",
        json={"name": "数学一班", "students": ["alice"]},
    )
    assert response.status_code == 200
    record = response.json()["class"]
    assert record["id"].startswith("cls_")
    assert record["teacher"] == "t1"
    assert record["students"] == ["alice"]

    rosters_file = isolated["users_file"].parent / "classes.json"
    assert rosters_file.is_file()


def test_list_classes_scoped_by_role(isolated: dict[str, Path]) -> None:
    _write_users(
        isolated["users_file"],
        {
            "t1": _user("u_t1", "teacher"),
            "t2": _user("u_t2", "teacher"),
            "alice": _user("u_alice", "student"),
        },
    )
    t1 = _client(role="teacher", username="t1")
    for name in ("甲班", "乙班"):
        assert (
            t1.post(
                "/api/v1/class-insights/classes",
                json={"name": name, "students": ["alice"]},
            ).status_code
            == 200
        )
    t2 = _client(role="teacher", username="t2")
    assert (
        t2.post(
            "/api/v1/class-insights/classes",
            json={"name": "丙班", "students": []},
        ).status_code
        == 200
    )

    assert len(t1.get("/api/v1/class-insights/classes").json()["classes"]) == 2
    assert len(t2.get("/api/v1/class-insights/classes").json()["classes"]) == 1
    admin = _client(role="admin", username="a1")
    assert len(admin.get("/api/v1/class-insights/classes").json()["classes"]) == 3


def test_update_delete_ownership(isolated: dict[str, Path]) -> None:
    _write_users(
        isolated["users_file"],
        {
            "t1": _user("u_t1", "teacher"),
            "t2": _user("u_t2", "teacher"),
            "alice": _user("u_alice", "student"),
        },
    )
    t1 = _client(role="teacher", username="t1")
    class_id = t1.post(
        "/api/v1/class-insights/classes",
        json={"name": "甲班", "students": ["alice"]},
    ).json()["class"]["id"]

    t2 = _client(role="teacher", username="t2")
    assert (
        t2.put(
            f"/api/v1/class-insights/classes/{class_id}", json={"name": "改名"}
        ).status_code
        == 403
    )
    assert t2.delete(f"/api/v1/class-insights/classes/{class_id}").status_code == 403

    admin = _client(role="admin", username="a1")
    response = admin.put(
        f"/api/v1/class-insights/classes/{class_id}", json={"name": "甲班改名"}
    )
    assert response.status_code == 200
    assert response.json()["class"]["name"] == "甲班改名"

    assert t1.delete(f"/api/v1/class-insights/classes/{class_id}").status_code == 200
    assert t1.get("/api/v1/class-insights/classes").json()["classes"] == []

    assert (
        t1.delete("/api/v1/class-insights/classes/cls_missing").status_code == 404
    )


def test_overview_class_filter_whitelist_semantics(
    isolated: dict[str, Path],
) -> None:
    _write_users(
        isolated["users_file"],
        {
            "t1": _user("u_t1", "teacher"),
            "alice": _user("u_alice", "student"),
            "bob": _user("u_bob", "student"),
            "carol": _user("u_carol", "student"),
        },
    )
    _write_progress(
        isolated["users_root"],
        "u_alice",
        "bookA",
        _progress("bookA", [("kp_a1", "KP A1", "memory", 0.9)]),
    )
    _write_progress(
        isolated["users_root"],
        "u_carol",
        "bookC",
        _progress("bookC", [("kp_c1", "KP C1", "memory", 0.8)]),
    )
    t1 = _client(role="teacher", username="t1")
    class_id = t1.post(
        "/api/v1/class-insights/classes",
        json={"name": "甲班", "students": ["alice", "bob"]},
    ).json()["class"]["id"]

    body = t1.get(
        "/api/v1/class-insights/overview", params={"class_id": class_id}
    ).json()
    assert body["class"] == {"id": class_id, "name": "甲班"}
    usernames = [s["username"] for s in body["students"]]
    # Whitelist view: rostered students appear even without learning data,
    # weakest first — the dataless bob (0%) surfaces at the top…
    assert usernames == ["bob", "alice"]
    assert body["students"][0]["no_data"] is True
    # …and a student outside the roster never leaks in.
    assert "carol" not in usernames

    # A roster entry whose account vanished after the save is skipped
    # fail-open (stale entry, mirroring the parent children linkage).
    _write_users(
        isolated["users_file"],
        {
            "t1": _user("u_t1", "teacher"),
            "alice": _user("u_alice", "student"),
            "carol": _user("u_carol", "student"),
        },
    )
    body = t1.get(
        "/api/v1/class-insights/overview", params={"class_id": class_id}
    ).json()
    assert [s["username"] for s in body["students"]] == ["alice"]


def test_overview_class_filter_permissions(isolated: dict[str, Path]) -> None:
    _write_users(
        isolated["users_file"],
        {
            "t1": _user("u_t1", "teacher"),
            "alice": _user("u_alice", "student"),
        },
    )
    t1 = _client(role="teacher", username="t1")
    class_id = t1.post(
        "/api/v1/class-insights/classes",
        json={"name": "甲班", "students": ["alice"]},
    ).json()["class"]["id"]

    t2 = _client(role="teacher", username="t2")
    assert (
        t2.get(
            "/api/v1/class-insights/overview", params={"class_id": class_id}
        ).status_code
        == 403
    )
    assert (
        t1.get(
            "/api/v1/class-insights/overview", params={"class_id": "cls_missing"}
        ).status_code
        == 404
    )


def test_unfiltered_overview_unchanged(isolated: dict[str, Path]) -> None:
    """Regression: the flat view still skips dataless students, no class key."""
    _write_users(
        isolated["users_file"],
        {
            "t1": _user("u_t1", "teacher"),
            "alice": _user("u_alice", "student"),
            "bob": _user("u_bob", "student"),
        },
    )
    _write_progress(
        isolated["users_root"],
        "u_alice",
        "bookA",
        _progress("bookA", [("kp_a1", "KP A1", "memory", 0.9)]),
    )
    client = _client(role="teacher", username="t1")
    body = client.get("/api/v1/class-insights/overview").json()
    assert [s["username"] for s in body["students"]] == ["alice"]
    assert "class" not in body


def test_save_user_preserves_parent_children(isolated: dict[str, Path]) -> None:
    """A password reset must not detach the family view (children linkage)."""
    users_file = isolated["users_file"]
    parent = _user("u_p1", "parent")
    parent["children"] = ["alice"]
    _write_users(users_file, {"p1": parent, "alice": _user("u_alice", "student")})

    record = identity.save_user("p1", "new-hash", role="parent")
    assert record["children"] == ["alice"]

    # Re-saving under a different role normalizes the linkage away, matching
    # the canonicalizer's parent-only whitelist.
    record = identity.save_user("p1", "newer-hash", role="user")
    assert "children" not in record

    # A fresh parent account has no linkage until one is recorded.
    record = identity.save_user("p2", "hash", role="parent")
    assert "children" not in record


def test_overview_reads_v2_mastery_store(isolated: dict[str, Path]) -> None:
    """The aggregation reads the V2 mastery sqlite store, not just legacy JSON."""
    from deeptutor.learning.storage import LearningStore

    _write_users(
        isolated["users_file"],
        {
            "t1": _user("u_t1", "teacher"),
            "alice": _user("u_alice", "student"),
        },
    )
    learning_dir = isolated["users_root"] / "u_alice" / "user" / "workspace" / "learning"
    store = LearningStore(root=learning_dir / "mastery")
    store.save(
        _progress(
            "bk_v2",
            [("kp_v2_1", "KP V2", "memory", 0.75)],
        )
    )

    t1 = _client(role="teacher", username="t1")
    class_id = t1.post(
        "/api/v1/class-insights/classes",
        json={"name": "v2班", "students": ["alice"]},
    ).json()["class"]["id"]
    body = t1.get(
        "/api/v1/class-insights/overview", params={"class_id": class_id}
    ).json()
    students = {s["username"]: s for s in body["students"]}
    assert "alice" in students
    alice = students["alice"]
    assert alice.get("no_data") is None
    assert alice["kp_total"] == 1
    assert alice["avg_mastery_pct"] == 75
