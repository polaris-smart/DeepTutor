"""Tests for the class-insights overview endpoint.

Covers the three contracts of ``GET /api/v1/class-insights/overview``:
multi-student aggregation across per-user learning stores, fail-open
behaviour when a student's data is missing or corrupt, and the
admin/teacher-only permission gate (a student role gets 403).
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
    """Redirect the user store and per-user workspace roots under tmp_path."""
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


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(class_insights_router, prefix="/api/v1/class-insights")
    # Success-path tests satisfy the admin/teacher gate at the dependency.
    app.dependency_overrides[require_admin_or_teacher] = lambda: TokenPayload(
        username="admin", role="admin", user_id="u_admin"
    )
    return TestClient(app)


def _write_users(users_file: Path, users: dict) -> None:
    users_file.parent.mkdir(parents=True, exist_ok=True)
    users_file.write_text(json.dumps(users, ensure_ascii=False), encoding="utf-8")


def _user(uid: str, role: str) -> dict:
    return {
        "id": uid,
        "hash": "$2b$12$fake-hash-for-tests",
        "role": role,
        "created_at": "2026-01-01T00:00:00+00:00",
        "disabled": False,
        "avatar": "",
    }


def _progress(
    book_id: str,
    kps: list[tuple[str, str, str, float | None]],
    *,
    qualitative_passed: dict[str, bool] | None = None,
    updated_at: float = 1735689600.0,
) -> LearningProgress:
    """Build a LearningProgress with one module of the given KPs.

    Each KP tuple is ``(id, name, type, mastery)``; a ``None`` mastery means
    the KP is qualitative (gated by ``qualitative_passed`` instead).
    """
    module_id = f"{book_id}_m1"
    modules = [
        LearningModule(
            id=module_id,
            name="Module 1",
            order=0,
            knowledge_points=[
                KnowledgePoint(
                    id=kp_id,
                    name=name,
                    type=KnowledgeType(kp_type),
                    module_id=module_id,
                )
                for kp_id, name, kp_type, _ in kps
            ],
        )
    ]
    mastery_levels = {
        kp_id: mastery for kp_id, _, _, mastery in kps if mastery is not None
    }
    return LearningProgress(
        book_id=book_id,
        modules=modules,
        mastery_levels=mastery_levels,
        qualitative_mastery=dict(qualitative_passed or {}),
        updated_at=updated_at,
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


def test_overview_aggregates_multiple_students(isolated: dict[str, Path]) -> None:
    """Only role=student accounts appear; mastery aggregates across their books."""
    _write_users(
        isolated["users_file"],
        {
            "alice": _user("u_alice", "student"),
            "bob": _user("u_bob", "student"),
            "t1": _user("u_t1", "teacher"),
            "a1": _user("u_a1", "admin"),
        },
    )
    # alice: 2 quantitative KPs (0.95, 0.5) + 1 qualitative KP passed (1.0)
    _write_progress(
        isolated["users_root"],
        "u_alice",
        "bookA",
        _progress(
            "bookA",
            [
                ("kp_a1", "KP A1", "memory", 0.95),
                ("kp_a2", "KP A2", "procedure", 0.5),
            ],
        ),
    )
    _write_progress(
        isolated["users_root"],
        "u_alice",
        "bookB",
        _progress(
            "bookB",
            [("kp_b1", "KP B1", "concept", None)],
            qualitative_passed={"kp_b1": True},
            updated_at=1735776000.0,
        ),
    )
    # bob: 1 quantitative KP at 0.7
    _write_progress(
        isolated["users_root"],
        "u_bob",
        "bookC",
        _progress("bookC", [("kp_c1", "KP C1", "memory", 0.7)]),
    )

    resp = _client().get("/api/v1/class-insights/overview")
    assert resp.status_code == 200
    body = resp.json()

    assert "generated_at" in body
    by_name = {student["username"]: student for student in body["students"]}
    assert set(by_name) == {"alice", "bob"}, "teacher/admin accounts must not appear"

    alice = by_name["alice"]
    assert alice["kp_total"] == 3
    assert alice["avg_mastery_pct"] == 82  # round((0.95 + 0.5 + 1.0) / 3 * 100)
    assert alice["weak"] == [{"kp_id": "kp_a2", "name": "KP A2", "mastery": 0.5}]
    assert alice["last_active"] == "2025-01-02T00:00:00+00:00"

    bob = by_name["bob"]
    assert bob["kp_total"] == 1
    assert bob["avg_mastery_pct"] == 70
    assert bob["weak"] == [{"kp_id": "kp_c1", "name": "KP C1", "mastery": 0.7}]

    # Weakest students surface first.
    assert [s["username"] for s in body["students"]] == ["bob", "alice"]


def test_overview_skips_students_without_readable_data(
    isolated: dict[str, Path],
) -> None:
    """Fail-open: missing/corrupt per-user data skips that student, never 500s."""
    _write_users(
        isolated["users_file"],
        {
            "alice": _user("u_alice", "student"),
            "carol": _user("u_carol", "student"),  # no workspace at all
            "dave": _user("u_dave", "student"),  # workspace, empty learning dir
            "eve": _user("u_eve", "student"),  # corrupt book + one valid book
        },
    )
    _write_progress(
        isolated["users_root"],
        "u_alice",
        "bookA",
        _progress("bookA", [("kp_a1", "KP A1", "memory", 0.8)]),
    )
    # dave: learning dir exists but holds no books
    (isolated["users_root"] / "u_dave" / "user" / "workspace" / "learning").mkdir(
        parents=True, exist_ok=True
    )
    # eve: one corrupt book (survived by a valid one), one valid book
    eve_dir = isolated["users_root"] / "u_eve" / "user" / "workspace" / "learning"
    eve_dir.mkdir(parents=True, exist_ok=True)
    (eve_dir / "broken.json").write_text("{not json", encoding="utf-8")
    _write_progress(
        isolated["users_root"],
        "u_eve",
        "bookE",
        _progress("bookE", [("kp_e1", "KP E1", "memory", 0.6)]),
    )

    resp = _client().get("/api/v1/class-insights/overview")
    assert resp.status_code == 200
    body = resp.json()

    by_name = {student["username"]: student for student in body["students"]}
    assert set(by_name) == {"alice", "eve"}, (
        "students with no readable learning data must be skipped (fail-open)"
    )
    assert by_name["eve"]["kp_total"] == 1
    assert by_name["eve"]["avg_mastery_pct"] == 60


def test_overview_rejects_student_role(monkeypatch: pytest.MonkeyPatch) -> None:
    """A student account gets 403; teacher and admin are allowed."""
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)

    app = FastAPI()
    app.include_router(class_insights_router, prefix="/api/v1/class-insights")

    monkeypatch.setattr(
        auth_router,
        "decode_token",
        lambda _t: TokenPayload(username="student1", role="student", user_id="u_student1"),
    )
    with TestClient(app) as client:
        resp = client.get(
            "/api/v1/class-insights/overview",
            headers={"Authorization": "Bearer student-token"},
        )
    assert resp.status_code == 403

    monkeypatch.setattr(
        auth_router,
        "decode_token",
        lambda _t: TokenPayload(username="teacher1", role="teacher", user_id="u_teacher1"),
    )
    with TestClient(app) as client:
        resp = client.get(
            "/api/v1/class-insights/overview",
            headers={"Authorization": "Bearer teacher-token"},
        )
    assert resp.status_code == 200
