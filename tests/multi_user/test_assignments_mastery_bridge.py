"""v1019: assignments 判分→掌握度回写桥 + KP id 命名对齐 + DELETE 作业路由.

断桥复现与修复验证（对应测通道实证 v1018 交付）:
- submit 判分后, 学生的 mastery path 必须收到 QuizAttempt（正确/错误都记）,
  map counts 才会从 new 迁移到 learning;
- KP id 生成源统一为 {bk}_m{i}_kp{j}（学生路径命名）, 不再产 ch 命名;
- DELETE /api/assignments/{id}: owner 门卫 + 提交状态门卫（force 兜底）.
"""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("fastapi")

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

import deeptutor.book.storage as storage_module
from deeptutor.api.routers import assignments as assignments_router
from deeptutor.api.routers import mastery_path as mastery_router
from deeptutor.api.routers import auth as auth_module
from deeptutor.api.routers.auth import require_auth
from deeptutor.learning.storage import LearningStore
from deeptutor.multi_user.classes import create_class
from deeptutor.multi_user.context import reset_current_user, set_current_user

BOOK = "bk_bridge"


@pytest.fixture
def client(mu_isolated_root, make_user, monkeypatch):
    teacher = make_user("t_tea", role="teacher", username="t_tea")
    student = make_user("t_stu", role="user", username="t_stu")
    users = {"t_tea": teacher, "t_stu": student}

    # 花名册门卫要求 live student 账号（users.json 即 store）。
    users_file = mu_isolated_root / "data" / "system" / "auth" / "users.json"
    users_file.parent.mkdir(parents=True, exist_ok=True)
    users_file.write_text(json.dumps({"t_stu": {"role": "student"}}), encoding="utf-8")

    storage_module._storages.clear()
    # 书必须真实存在且归教师（v1021 起 import-from-book 校验书访问，无授权
    # 403）：在 t_tea workspace 建 BOOK，教师 import 走 own 分支；学生 import
    # 同一本书走既有 grant 机制（read 授权，peer 解析）。
    from deeptutor.book.models import Book
    from deeptutor.book.storage import BookStorage
    from deeptutor.multi_user import book_access as book_access_module
    from deeptutor.multi_user.book_permission import BookPermission
    from deeptutor.multi_user.paths import get_path_service_for_scope

    BookStorage(path_service=get_path_service_for_scope(teacher.scope)).save_book(
        Book(id=BOOK, title="桥测试书")
    )
    # book_access._auth_enabled() 读 services.auth 的 AUTH_ENABLED（非 routers
    # .auth 副本），学生 peer grant 解析需要它为 True。
    from deeptutor.services import auth as services_auth

    monkeypatch.setattr(services_auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(
        book_access_module,
        "permission_for_user",
        lambda user: (
            BookPermission(books=((BOOK, "read"),))
            if user.id == "t_stu"
            else BookPermission()
        ),
    )
    monkeypatch.setattr(
        mastery_router, "_cancel_active_learning_turn", lambda book_id: asyncio.sleep(0)
    )
    # require_teacher 的 role 门卫只在 AUTH_ENABLED=true 时生效; 打开它让
    # 教师/学生角色语义与生产一致（require_auth 本身被 override, 不走 JWT）。
    monkeypatch.setattr(auth_module, "AUTH_ENABLED", True)

    async def install_user(request: Request):
        current = users[request.headers.get("x-test-user", "t_stu")]
        token = set_current_user(current)
        try:
            yield current
        finally:
            reset_current_user(token)

    app = FastAPI()
    app.include_router(
        assignments_router.router, prefix="/api/assignments", dependencies=[Depends(install_user)]
    )
    app.include_router(
        mastery_router.router, prefix="/api/v1/learning", dependencies=[Depends(install_user)]
    )
    app.dependency_overrides[require_auth] = install_user
    return TestClient(app)


def _import_book(client: TestClient, username: str, chapters: list[dict]) -> dict:
    resp = client.post(
        f"/api/v1/learning/progress/{BOOK}/import-from-book",
        json={"chapters": chapters},
        headers={"x-test-user": username},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _bind_question(client: TestClient, username: str, kp_id: str) -> None:
    resp = client.put(
        f"/api/v1/learning/progress/{BOOK}/question-bank",
        json={
            "knowledge_point_id": kp_id,
            "questions": [
                {
                    "question": "集合 {1,2,3} 的元素个数是?",
                    "question_type": "choice",
                    "options": {"A": "1", "B": "2", "C": "3"},
                    "answer": "C",
                    "explanation": "count them",
                }
            ],
        },
        headers={"x-test-user": username},
    )
    assert resp.status_code == 200, resp.text


def _store_as(users: dict, username: str) -> LearningStore:
    token = set_current_user(users[username])
    try:
        return LearningStore()
    finally:
        reset_current_user(token)


@pytest.fixture
def seeded(client, mu_isolated_root, make_user):
    """教师+学生各建同名路径、绑题、建班、布置作业, 返回上下文。"""
    users = {
        "t_tea": make_user("t_tea", role="teacher", username="t_tea"),
        "t_stu": make_user("t_stu", role="user", username="t_stu"),
    }
    _import_book(client, "t_tea", [{"title": "ch1", "knowledge_points": ["kp-a", "kp-b"]}])
    _import_book(client, "t_stu", [{"title": "ch1", "knowledge_points": ["kp-a", "kp-b"]}])

    teacher_store = _store_as(users, "t_tea")
    progress = teacher_store.load(BOOK)
    assert progress is not None
    kp_id = progress.modules[0].knowledge_points[0].id
    # 对齐后的生成源: 学生路径命名 {bk}_m{i}_kp{j}, 不再是 {bk}_ch{i}_kp{j}。
    assert kp_id == f"{BOOK}_m0_kp0"
    _bind_question(client, "t_tea", kp_id)

    cls = create_class(name="bridge班", teacher="t_tea", students=["t_stu"])
    resp = client.post(
        "/api/assignments",
        json={"class_id": cls["id"], "book_id": BOOK, "title": "桥测试", "kp_ids": [kp_id]},
        headers={"x-test-user": "t_tea"},
    )
    assert resp.status_code == 200, resp.text
    assignment_id = resp.json()["assignment"]["assignment_id"]
    return {"users": users, "assignment_id": assignment_id, "kp_id": kp_id}


def test_submit_correct_moves_new_to_learning(client, seeded) -> None:
    store = _store_as(seeded["users"], "t_stu")
    before = store.load(BOOK)
    assert before is not None
    assert all(a.knowledge_point_id != seeded["kp_id"] for a in before.quiz_attempts)

    resp = client.post(
        f"/api/assignments/{seeded['assignment_id']}/submit",
        json={"answers": [{"q_idx": 0, "answer": "C"}]},
        headers={"x-test-user": "t_stu"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["correct_count"] == 1
    assert body["mastery_link"]["recorded"] == 1
    assert body["error_link"]["linked"] == 0

    progress = _store_as(seeded["users"], "t_stu").load(BOOK)
    attempts = [
        a for a in progress.quiz_attempts if a.knowledge_point_id == seeded["kp_id"]
    ]
    assert len(attempts) == 1 and attempts[0].is_correct
    assert attempts[0].self_attribution == f"assignment:{seeded['assignment_id']}"

    counts = {
        entry["id"]: entry["status"]
        for module in progress.modules
        for entry in (
            [
                {"id": kp.id, "status": _status(progress, kp)}
                for kp in module.knowledge_points
            ]
        )
    }
    # 桥通判据: 答对的 KP 从 new 迁移到 learning, 未触及的 KP 保持 new。
    assert counts[seeded["kp_id"]] == "learning"
    other_kp = f"{BOOK}_m0_kp1"
    assert counts[other_kp] == "new"


def _status(progress, kp) -> str:
    from deeptutor.learning.policy import objective_status

    return objective_status(progress, kp)


def test_submit_wrong_keeps_error_record_and_marks_learning(client, seeded) -> None:
    resp = client.post(
        f"/api/assignments/{seeded['assignment_id']}/submit",
        json={"answers": [{"q_idx": 0, "answer": "A"}]},
        headers={"x-test-user": "t_stu"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["correct_count"] == 0
    assert body["error_link"]["linked"] == 1
    assert body["mastery_link"]["recorded"] == 1

    progress = _store_as(seeded["users"], "t_stu").load(BOOK)
    active = [
        r for r in progress.error_records if r.knowledge_point_id == seeded["kp_id"]
    ]
    assert len(active) == 1 and active[0].status == "active"
    attempts = [
        a for a in progress.quiz_attempts if a.knowledge_point_id == seeded["kp_id"]
    ]
    assert len(attempts) == 1 and not attempts[0].is_correct


def test_submit_resubmission_replaces_attempts(client, seeded) -> None:
    for answer in ("A", "C"):
        resp = client.post(
            f"/api/assignments/{seeded['assignment_id']}/submit",
            json={"answers": [{"q_idx": 0, "answer": answer}]},
            headers={"x-test-user": "t_stu"},
        )
        assert resp.status_code == 200, resp.text
    progress = _store_as(seeded["users"], "t_stu").load(BOOK)
    attempts = [
        a for a in progress.quiz_attempts if a.knowledge_point_id == seeded["kp_id"]
    ]
    assert len(attempts) == 1 and attempts[0].is_correct


def test_foreign_kp_id_is_skipped_not_written(client, seeded) -> None:
    # 教师 store 里的第二个 KP 未绑题——改绑题到它, 作业只含它;
    # 学生提交后 kp 匹配。再构造一个"学生路径中没有的 kp_id"场景:
    # 直接对一个不存在的 assignment kp 断言 skip 上报。
    store = _store_as(seeded["users"], "t_stu")
    from deeptutor.api.routers.assignments import _record_mastery_attempts

    report = _record_mastery_attempts(
        {"assignment_id": seeded["assignment_id"], "book_id": BOOK},
        [{"kp_id": "ghost_kp", "q_idx": 0, "answer": "C", "correct": True}],
    )
    assert report["recorded"] == 0
    assert report["skipped"] == [{"kp_id": "ghost_kp", "reason": "kp_not_in_progress"}]
    progress = store.load(BOOK)
    assert all(a.knowledge_point_id != "ghost_kp" for a in progress.quiz_attempts)


def test_delete_guards_and_cleanup(client, seeded) -> None:
    assignment_id = seeded["assignment_id"]

    # 学生（非教师 token → require_teacher 拒绝; 这里用另一个教师名更直接）
    # owner 门卫: 不存在的 id → 404
    resp = client.delete(
        f"/api/assignments/asg_missing", headers={"x-test-user": "t_tea"}
    )
    assert resp.status_code == 404

    # 提交前删除: 无状态门卫拦截
    resp = client.delete(f"/api/assignments/{assignment_id}", headers={"x-test-user": "t_tea"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True

    # 删除后学生 /mine 不再可见
    resp = client.get("/api/assignments/mine", headers={"x-test-user": "t_stu"})
    assert resp.status_code == 200
    assert all(
        a["assignment_id"] != assignment_id for a in resp.json()["assignments"]
    )


def test_delete_requires_submitted_force(client, mu_isolated_root, make_user, monkeypatch):
    # 复用 seeded 前半段: 提交后再删, 无 force → 409, force=true → 200
    seeded = None
    users = {
        "t_tea": make_user("t_tea", role="teacher", username="t_tea"),
        "t_stu": make_user("t_stu", role="user", username="t_stu"),
    }
    users_file = mu_isolated_root / "data" / "system" / "auth" / "users.json"
    users_file.parent.mkdir(parents=True, exist_ok=True)
    users_file.write_text(json.dumps({"t_stu": {"role": "student"}}), encoding="utf-8")
    storage_module._storages.clear()
    # 同 client fixture：书归教师 + 学生 read grant（v1021 书访问校验）。
    from deeptutor.book.models import Book
    from deeptutor.book.storage import BookStorage
    from deeptutor.multi_user import book_access as book_access_module
    from deeptutor.multi_user.book_permission import BookPermission
    from deeptutor.multi_user.paths import get_path_service_for_scope

    BookStorage(
        path_service=get_path_service_for_scope(users["t_tea"].scope)
    ).save_book(Book(id=BOOK, title="桥测试书"))
    # 同 client fixture：services.auth.AUTH_ENABLED 供 peer grant 解析。
    from deeptutor.services import auth as services_auth

    monkeypatch.setattr(services_auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(
        book_access_module,
        "permission_for_user",
        lambda user: (
            BookPermission(books=((BOOK, "read"),))
            if user.id == "t_stu"
            else BookPermission()
        ),
    )
    monkeypatch.setattr(
        mastery_router, "_cancel_active_learning_turn", lambda book_id: asyncio.sleep(0)
    )
    # require_teacher 的 role 门卫只在 AUTH_ENABLED=true 时生效; 打开它让
    # 教师/学生角色语义与生产一致（require_auth 本身被 override, 不走 JWT）。
    monkeypatch.setattr(auth_module, "AUTH_ENABLED", True)

    async def install_user(request: Request):
        current = users[request.headers.get("x-test-user", "t_stu")]
        token = set_current_user(current)
        try:
            yield current
        finally:
            reset_current_user(token)

    app = FastAPI()
    app.include_router(
        assignments_router.router, prefix="/api/assignments", dependencies=[Depends(install_user)]
    )
    app.include_router(
        mastery_router.router, prefix="/api/v1/learning", dependencies=[Depends(install_user)]
    )
    app.dependency_overrides[require_auth] = install_user
    client = TestClient(app)

    _import_book(client, "t_tea", [{"title": "ch1", "knowledge_points": ["kp-a"]}])
    _import_book(client, "t_stu", [{"title": "ch1", "knowledge_points": ["kp-a"]}])
    kp_id = f"{BOOK}_m0_kp0"
    _bind_question(client, "t_tea", kp_id)
    cls = create_class(name="force班", teacher="t_tea", students=["t_stu"])
    resp = client.post(
        "/api/assignments",
        json={"class_id": cls["id"], "book_id": BOOK, "title": "force", "kp_ids": [kp_id]},
        headers={"x-test-user": "t_tea"},
    )
    assignment_id = resp.json()["assignment"]["assignment_id"]
    resp = client.post(
        f"/api/assignments/{assignment_id}/submit",
        json={"answers": [{"q_idx": 0, "answer": "C"}]},
        headers={"x-test-user": "t_stu"},
    )
    assert resp.status_code == 200

    resp = client.delete(f"/api/assignments/{assignment_id}", headers={"x-test-user": "t_tea"})
    assert resp.status_code == 409
    resp = client.delete(
        f"/api/assignments/{assignment_id}?force=true", headers={"x-test-user": "t_tea"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["removed_submissions"] == 1


# ---------------------------------------------------------------------------
# v1020: KP id 归一 —— 教师侧存量 progress 仍是 legacy `_ch{i}_kp{j}` 命名
# （v1019 只改生成源, 不改写已落盘记录）, item 带的 ch id 与学生路径 m id
# 同根同序位, 桥必须经学生 active path 的序位网格解析后回写。
# ---------------------------------------------------------------------------


def _rewrite_teacher_progress_legacy_ch(users: dict, kp_count: int = 2) -> str:
    """把教师侧 progress 改写成 v1019 之前的 `_ch{i}_kp{j}` 存量形态。"""
    store = _store_as(users, "t_tea")

    def apply(tx) -> None:
        for module in tx.progress.modules:
            module.id = module.id.replace("_m", "_ch", 1)
            for position, kp in enumerate(module.knowledge_points):
                kp.module_id = module.id
                kp.id = f"{module.id}_kp{position}"
        tx.touch()

    store.mutate(BOOK, apply, create=False)
    return f"{BOOK}_ch0_kp0"


def test_legacy_ch_item_resolves_to_student_path(client, seeded) -> None:
    """topic_* 场景: 教师 ch 前缀 item + 学生 m 前缀路径 → recorded=1。

    seeded 里教师/学生同走对齐后的 m 命名; 这里把教师侧改回存量 ch 命名
    并重绑题、重建作业（item 的 kp_id 即 ch id）, 学生提交后:
    attempt 落在学生路径 `_m0_kp0` 上, counts new→learning。
    """
    users = seeded["users"]
    ch_kp = _rewrite_teacher_progress_legacy_ch(users)
    assert ch_kp == f"{BOOK}_ch0_kp0"
    _bind_question(client, "t_tea", ch_kp)
    cls = create_class(name="归一班", teacher="t_tea", students=["t_stu"])
    resp = client.post(
        "/api/assignments",
        json={"class_id": cls["id"], "book_id": BOOK, "title": "归一", "kp_ids": [ch_kp]},
        headers={"x-test-user": "t_tea"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["skipped"] == []
    assignment_id = body["assignment"]["assignment_id"]

    resp = client.post(
        f"/api/assignments/{assignment_id}/submit",
        json={"answers": [{"q_idx": 0, "answer": "C"}]},
        headers={"x-test-user": "t_stu"},
    )
    assert resp.status_code == 200, resp.text
    link = resp.json()["mastery_link"]
    # 回写判据: ch 前缀 item 解析到路径 id, 不再 skipped。
    assert link["recorded"] == 1, link
    assert link["skipped"] == []

    path_kp = f"{BOOK}_m0_kp0"
    progress = _store_as(users, "t_stu").load(BOOK)
    attempts = [a for a in progress.quiz_attempts if a.knowledge_point_id == path_kp]
    assert len(attempts) == 1 and attempts[0].is_correct
    # 悬挂 id 零写入: ch id 不得出现在学生路径里。
    assert all(a.knowledge_point_id != ch_kp for a in progress.quiz_attempts)
    # counts 迁移: 答对 KP new→learning。
    assert _status(progress, next(kp for m in progress.modules for kp in m.knowledge_points if kp.id == path_kp)) == "learning"


def test_cross_root_ch_item_is_not_remapped(client, seeded) -> None:
    """异根（不同书）的 ch id 不许经序位映射写进当前路径。"""
    from deeptutor.api.routers.assignments import _record_mastery_attempts

    report = _record_mastery_attempts(
        {"assignment_id": seeded["assignment_id"], "book_id": BOOK},
        [{"kp_id": "bk_other_ch0_kp0", "q_idx": 0, "answer": "C", "correct": True}],
    )
    assert report["recorded"] == 0
    assert report["skipped"] == [
        {"kp_id": "bk_other_ch0_kp0", "reason": "kp_not_in_progress"}
    ]
    progress = _store_as(seeded["users"], "t_stu").load(BOOK)
    assert all(a.knowledge_point_id != "bk_other_ch0_kp0" for a in progress.quiz_attempts)
