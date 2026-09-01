"""Tests for POST /api/v1/courses/{course_id}/copy (K12 课程复制).

Covers: fresh-instance semantics (syllabus progress reset, agent notes not
copied, ``copied_from`` provenance), the staff template pool as copy source,
and the target-permission matrix (admin any, parent only their recorded
children, teacher/student self only).
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

FastAPI = pytest.importorskip("fastapi").FastAPI
TestClient = pytest.importorskip("fastapi.testclient").TestClient

courses_router_module = importlib.import_module("deeptutor.api.routers.courses")
courses_service_module = importlib.import_module("deeptutor.services.courses")
courses_router = courses_router_module.router

from deeptutor.multi_user import identity, paths as mu_paths
from deeptutor.services.auth import TokenPayload
from deeptutor.services.courses import CourseService


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Isolate the account store and every user workspace under tmp_path."""
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
    users_file.parent.mkdir(parents=True, exist_ok=True)
    users_file.write_text(
        json.dumps(
            {
                "t1": _user("u_t1", "teacher"),
                "t2": _user("u_t2", "teacher"),
                "p1": {**_user("u_p1", "parent"), "children": ["s1", "s2"]},
                "s1": _user("u_s1", "student"),
                "s2": _user("u_s2", "student"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return {"users_root": users_root, "users_file": users_file}


def _user(uid: str, role: str) -> dict:
    return {
        "id": uid,
        "hash": "$2b$12$fake-hash-for-tests",
        "role": role,
        "created_at": "2026-01-01T00:00:00+00:00",
        "disabled": False,
        "avatar": "",
    }


def _workspace_service(isolated: dict[str, Path], uid: str) -> CourseService:
    """A course service pointed at one user's workspace (real path layout)."""
    from deeptutor.services.path_service import PathService

    scope_root = isolated["users_root"] / uid
    root = PathService(workspace_root=scope_root).get_workspace_dir() / "courses"
    return CourseService(root=root)


@pytest.fixture
def caller_service(isolated: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> CourseService:
    """The caller's own workspace service — what the router resolves by default."""
    service = _workspace_service(isolated, "u_t2")
    monkeypatch.setattr(courses_router_module, "get_course_service", lambda: service)
    monkeypatch.setattr(courses_service_module, "get_course_service", lambda: service)
    return service


def _client(role: str = "teacher", username: str = "t2") -> TestClient:
    app = FastAPI()
    app.include_router(courses_router, prefix="/api/v1/courses")
    app.dependency_overrides[
        courses_router_module.require_auth
    ] = lambda: TokenPayload(username=username, role=role, user_id=f"u_{username}")
    return TestClient(app)


def _seed_source(isolated: dict[str, Path]) -> dict:
    """A teacher-authored course with instructions, syllabus progress, resources."""
    service = _workspace_service(isolated, "u_t1")
    source = service.create(
        name="数学函数必修一",
        description="函数概念与性质",
        instructions="使用人教 A 版记号",
        default_capability="course_study",
    )
    service.set_syllabus(
        source.id,
        units=[
            {"title": "集合", "topics": ["交集", "并集"]},
            {"title": "函数概念", "topics": ["定义域"]},
        ],
    )
    source = service.get(source.id)
    service.set_unit_covered(source.id, source.syllabus[0].id, True)
    service.append_agent_note(source.id, "学生爱好几何直观")
    service.attach_resource(source.id, kind="book", ref_id="bk_demo", label="必修一教材")
    return service.get(source.id).to_dict()


def test_copy_from_teacher_pool_is_a_fresh_instance(
    isolated: dict[str, Path], caller_service: CourseService
) -> None:
    source = _seed_source(isolated)
    client = _client(role="teacher", username="t2")

    response = client.post(
        f"/api/v1/courses/{source['id']}/copy", json={"name": "t2 的函数课"}
    )
    assert response.status_code == 200
    copied = response.json()["course"]
    assert copied["name"] == "t2 的函数课"
    assert copied["copied_from"] == source["id"]
    assert copied["instructions"] == "使用人教 A 版记号"
    assert copied["default_capability"] == "course_study"
    # Learning state resets: covered flags are gone, agent notes never travel.
    assert all(unit["covered"] is False for unit in copied["syllabus"])
    assert copied["agent_notes"] == ""
    # Resource references re-attached.
    assert [(r["kind"], r["ref_id"]) for r in copied["resources"]] == [("book", "bk_demo")]
    # The source stays untouched in t1's workspace.
    assert courses_service_module.CourseService(
        root=courses_router_module.workspace_courses_root("u_t1")
    ).get(source["id"]).name == "数学函数必修一"


def test_copy_target_matrix(isolated: dict[str, Path], caller_service: CourseService) -> None:
    source = _seed_source(isolated)

    # Teacher → someone else's account: 403.
    response = _client(role="teacher", username="t2").post(
        f"/api/v1/courses/{source['id']}/copy", json={"target_username": "s1"}
    )
    assert response.status_code == 403

    # Parent → recorded child: 200; parent → stranger: 403.
    response = _client(role="parent", username="p1").post(
        f"/api/v1/courses/{source['id']}/copy", json={"target_username": "s1"}
    )
    assert response.status_code == 200
    response = _client(role="parent", username="p1").post(
        f"/api/v1/courses/{source['id']}/copy", json={"target_username": "t2"}
    )
    assert response.status_code == 403

    # Admin → any account: 200.
    response = _client(role="admin", username="a1").post(
        f"/api/v1/courses/{source['id']}/copy", json={"target_username": "s2"}
    )
    assert response.status_code == 200

    # The copies landed in the targets' own workspaces.
    for uid in ("u_s1", "u_s2"):
        service = _workspace_service(isolated, uid)
        assert len(service.list_courses()) == 1
        assert service.list_courses()[0].copied_from == source["id"]


def test_copy_name_conflict_409(isolated: dict[str, Path], caller_service: CourseService) -> None:
    source = _seed_source(isolated)
    caller_service.create(name="数学函数必修一")
    response = _client(role="teacher", username="t2").post(
        f"/api/v1/courses/{source['id']}/copy", json={}
    )
    assert response.status_code == 409


def test_copy_unknown_course_404(caller_service: CourseService) -> None:
    response = _client(role="teacher", username="t2").post(
        "/api/v1/courses/course_missing/copy", json={}
    )
    assert response.status_code == 404


def test_self_duplicate_within_own_workspace(
    isolated: dict[str, Path], caller_service: CourseService
) -> None:
    """A course in the caller's own workspace duplicates without the pool scan."""
    source = caller_service.create(name="自用课", description="d")
    caller_service.set_syllabus(source.id, units=[{"title": "单元一"}])
    response = _client(role="teacher", username="t2").post(
        f"/api/v1/courses/{source.id}/copy", json={"name": "自用课副本"}
    )
    assert response.status_code == 200
    copied = response.json()["course"]
    assert copied["copied_from"] == source.id
    assert [u["title"] for u in copied["syllabus"]] == ["单元一"]
