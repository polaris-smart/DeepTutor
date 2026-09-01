"""Tests for the K12 question-bank binding (错题闭环题源).

Covers: PUT /learning/progress/{book}/question-bank stores verbatim items on
the KP; mastery_quiz prefers a bound bank item over the model's draft
(rotation via meta.bank_cursor); unknown KP → 404.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

FastAPI = pytest.importorskip("fastapi").FastAPI
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from deeptutor.api.routers import mastery_path as mastery_router_module
from deeptutor.api.routers.auth import require_auth
from deeptutor.learning.models import (
    KnowledgePoint,
    KnowledgeType,
    LearningModule,
    LearningProgress,
)
from deeptutor.learning.service import LearningService
from deeptutor.learning.storage import LearningStore
from deeptutor.services.auth import TokenPayload
from deeptutor.services.path_service import PathService


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    users_root = tmp_path / "data" / "users"
    system_root = tmp_path / "data" / "system"
    users_file = system_root / "auth" / "users.json"
    monkeypatch.setattr("deeptutor.multi_user.paths.USERS_ROOT", users_root)
    monkeypatch.setattr("deeptutor.multi_user.paths.SYSTEM_ROOT", system_root)
    monkeypatch.setattr("deeptutor.multi_user.paths._path_services", {})
    monkeypatch.setattr("deeptutor.multi_user.identity.USERS_FILE", users_file)
    monkeypatch.setattr(
        "deeptutor.multi_user.identity.LEGACY_USERS_FILE",
        tmp_path / "data" / "user" / "auth_users.json",
    )
    users_file.parent.mkdir(parents=True, exist_ok=True)
    users_file.write_text(
        json.dumps({"alice": _user("u_alice", "student")}, ensure_ascii=False),
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


def _progress(book_id: str) -> LearningProgress:
    module_id = f"{book_id}_m1"
    return LearningProgress(
        book_id=book_id,
        modules=[
            LearningModule(
                id=module_id,
                name="M1",
                order=0,
                knowledge_points=[
                    KnowledgePoint(
                        id=f"{book_id}_kp0",
                        name="函数的单调性",
                        type=KnowledgeType("concept"),
                        module_id=module_id,
                    )
                ],
            )
        ],
        mastery_levels={},
        qualitative_mastery={},
    )


def test_question_bank_binds_and_quiz_prefers_bank(
    isolated: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from deeptutor.capabilities.mastery import tools as mtools
    from deeptutor.api.routers.auth import require_auth as _ra

    learning_dir = isolated["users_root"] / "u_alice" / "user" / "workspace" / "learning"
    store = LearningStore(root=learning_dir / "mastery")
    store.save(_progress("bk_qb"))

    app = FastAPI()
    app.include_router(mastery_router_module.router, prefix="/api/v1/learning")
    app.dependency_overrides[require_auth] = lambda: TokenPayload(
        username="alice", role="student", user_id="u_alice"
    )
    monkeypatch.setattr(
        mastery_router_module,
        "get_learning_service",
        lambda: LearningService(store=LearningStore(root=learning_dir / "mastery")),
    )
    client = TestClient(app)

    # Bind two bank items.
    response = client.put(
        "/api/v1/learning/progress/bk_qb/question-bank",
        json={
            "knowledge_point_id": "bk_qb_kp0",
            "questions": [
                {
                    "question": "国家智慧教育平台入库原题一：下列哪个是增函数？",
                    "question_type": "choice",
                    "options": {"A": "y=-x", "B": "y=2^x", "C": "y=1/x", "D": "y=-x^2"},
                    "answer": "B",
                    "explanation": "指数底数大于1递增。",
                    "difficulty": "基础",
                },
                {
                    "question": "入库原题二：单调性定义中,x1<x2 则 f(x1)<f(x2) 是？",
                    "question_type": "short",
                    "answer": "增函数",
                },
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 2

    # Stored on the KP.
    progress = store.load("bk_qb")
    kp = progress.modules[0].knowledge_points[0]
    assert len(kp.meta["question_bank"]) == 2

    # mastery_quiz prefers the bank item over the model draft: the tool's
    # execute rewrites question/options/answer with rotation.
    import deeptutor.learning.storage as ls_module

    original_init = ls_module.LearningStore.__init__
    monkeypatch.setattr(
        ls_module.LearningStore,
        "__init__",
        lambda self, root=None: original_init(self, root=learning_dir / "mastery"),
    )
    tool = mtools.MasteryQuizTool()
    result = __import__("asyncio").run(
        tool.execute(
            _mastery_path_id="bk_qb",
            knowledge_point_id="bk_qb_kp0",
            question="模型拟的题（应被题库原题替换）",
            question_type="choice",
            expected_answer="C",
            options=["A: 干扰", "B: 干扰", "C: 干扰", "D: 干扰"],
        )
    )
    assert result.success, result.content[:300]
    payload = json.loads(result.content)
    assert payload["question"] == "国家智慧教育平台入库原题一：下列哪个是增函数？"
    # Normalized options render label: body pairs — the bank body must be there.
    rendered = str(payload["options"])
    assert "y=2^x" in rendered
    assert "模型拟的题" not in rendered
    # Rotation: cursor advanced to the second item.
    assert store.load("bk_qb").modules[0].knowledge_points[0].meta["bank_cursor"] == 1

    # Unknown KP → 404.
    assert (
        client.put(
            "/api/v1/learning/progress/bk_qb/question-bank",
            json={"knowledge_point_id": "kp_missing", "questions": [
                {"question": "题目足够长的题干示例。", "answer": "A"}
            ]},
        ).status_code
        == 404
    )


import asyncio
import json
