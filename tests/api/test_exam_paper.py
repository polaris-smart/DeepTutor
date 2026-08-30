"""API tests for the 组卷 (exam paper assembly) line.

Covers:

* ``GET /api/v1/knowledge/{kb_name}/questions/by-struct`` — doc_intel
  question nodes under a textbook structure path, ``is_question`` filtering,
  and the fail-open empty+hint behaviour for KBs without doc_intel metadata.
* ``POST /api/v1/exam-paper/assemble`` — docx generation (non-empty bytes,
  ``PK`` ZIP magic), the teacher-only answer sheet, and fail-open handling of
  missing ids.
* Route registration smoke test on the assembled ``deeptutor.api.main`` app.

The docstore is faked with ``SimpleNamespace`` nodes exactly like
``tests/api/test_textbook_tree_api.py``, so no index or network is needed.
"""

from __future__ import annotations

import importlib
import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

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
    with patch("deeptutor.services.config.load_config_with_main", return_value={}):
        knowledge_module = importlib.import_module("deeptutor.api.routers.knowledge")
        exam_paper_module = importlib.import_module("deeptutor.api.routers.exam_paper")
else:  # pragma: no cover - guarded by pytestmark
    knowledge_module = None
    exam_paper_module = None


class _Node:
    """Minimal LlamaIndex node stand-in with the node_id/metadata/text surface."""

    def __init__(
        self,
        node_id: str,
        *,
        metadata: dict,
        text: str = "",
    ) -> None:
        self.node_id = node_id
        self.metadata = metadata
        self.text = text

    def get_content(self) -> str:
        return self.text


def _docstore(*nodes: _Node):
    return SimpleNamespace(docs={node.node_id: node for node in nodes})


def _questions_client(monkeypatch: pytest.MonkeyPatch, docstore) -> TestClient:
    app = FastAPI()
    app.include_router(knowledge_module.router, prefix="/api/v1/knowledge")
    monkeypatch.setattr(
        knowledge_module,
        "_load_kb_docstore",
        lambda kb_name: (kb_name, docstore),
    )
    return TestClient(app)


def _questions_client_without_docstore(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = FastAPI()
    app.include_router(knowledge_module.router, prefix="/api/v1/knowledge")
    monkeypatch.setattr(
        knowledge_module,
        "_load_kb_docstore",
        lambda kb_name: (kb_name, None),
    )
    return TestClient(app)


def _assemble_client(
    monkeypatch: pytest.MonkeyPatch,
    docstore,
    *,
    role: str | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(exam_paper_module.router, prefix="/api/v1/exam-paper")
    monkeypatch.setattr(exam_paper_module, "_load_docstore", lambda kb_name: (kb_name, docstore))
    if role is not None:
        monkeypatch.setattr(
            exam_paper_module,
            "get_current_user",
            lambda: SimpleNamespace(role=role),
        )
    return TestClient(app)


def _teacher_edition_docstore() -> SimpleNamespace:
    """A textbook chunked by doc_intel: two questions + two answer blocks."""
    return _docstore(
        _Node(
            "n-q1",
            text="1. 计算 3 + 5 的值。",
            metadata={
                "file_name": "数学必修一_教师版.pdf",
                "struct_path": "必修一/第1章 集合/1.1 集合的概念",
                "is_question": True,
                "q_id": "P3-1",
                "q_type": "计算题",
                "has_answer": True,
            },
        ),
        _Node(
            "n-a1",
            text="答案：8",
            metadata={
                "file_name": "数学必修一_教师版.pdf",
                "struct_path": "必修一/第1章 集合/1.1 集合的概念",
                "is_answer": True,
                "q_id": "P3-1",
            },
        ),
        _Node(
            "n-q2",
            text="2. 判断下列说法是否正确。",
            metadata={
                "file_name": "数学必修一_教师版.pdf",
                "struct_path": "必修一/第1章 集合/1.2 集合间的基本关系",
                "is_question": True,
                "q_id": "P3-2",
                "q_type": "判断题",
            },
        ),
        _Node(
            "n-q11",
            text="11. 第11章跨章题目不应被“第1章”前缀误伤。",
            metadata={
                "file_name": "数学必修二_教师版.pdf",
                "struct_path": "必修二/第11章 统计/11.1 抽样",
                "is_question": True,
                "q_id": "P5-11",
            },
        ),
        # A plain body chunk without doc_intel markers.
        _Node(
            "n-body",
            text="本节学习目标……",
            metadata={"file_name": "数学必修一_教师版.pdf", "struct_path": "必修一/第1章 集合"},
        ),
    )


# ── GET /knowledge/{kb}/questions/by-struct ────────────────────────────────


def test_questions_by_struct_returns_is_question_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    with _questions_client(monkeypatch, _teacher_edition_docstore()) as client:
        response = client.get(
            "/api/v1/knowledge/数学/questions/by-struct",
            params={"struct_path": "必修一/第1章"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["kb_name"] == "数学"
    assert payload["struct_path"] == "必修一/第1章"
    assert payload["has_doc_intel"] is True
    assert payload["hint"] == ""
    questions = payload["questions"]
    assert [q["q_id"] for q in questions] == ["P3-1", "P3-2"]
    first = questions[0]
    assert first["node_id"] == "n-q1"
    assert first["text"].startswith("1. 计算 3 + 5")
    assert first["question_type"] == "计算题"
    assert first["difficulty"] == ""
    assert first["struct_path"] == "必修一/第1章 集合/1.1 集合的概念"
    assert first["has_answer"] is True
    assert first["file_name"] == "数学必修一_教师版.pdf"


def test_questions_by_struct_reads_serialized_qa_split_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production indexes keep per-block qa_split data in ``di_block_meta``."""
    metadata = {
        "file_name": "数学必修一_教师版.pdf",
        "di_block_meta": json.dumps(
            [
                {
                    "is_question": True,
                    "q_id": "P3-1",
                    "q_type": "计算题",
                    "struct_path": "必修一/第1章 集合/1.1 集合的概念",
                },
                # The persisted slim format keeps an answer's q_id but drops
                # is_answer. It must not create a second question.
                {
                    "q_id": "P3-1",
                    "struct_path": "必修一/第1章 集合/1.1 集合的概念",
                },
            ],
            ensure_ascii=False,
        ),
        "di_q_ids": "P3-1,P3-1",
    }
    docstore = _docstore(
        _Node(
            "n-q1",
            text="1. 计算 3 + 5 的值。",
            metadata=metadata,
        ),
        # LlamaIndex chunks inherit the same document metadata. The endpoint
        # must not emit every serialized question once per chunk.
        _Node("n-other", text="本节学习目标……", metadata=dict(metadata)),
    )
    with _questions_client(monkeypatch, docstore) as client:
        response = client.get(
            "/api/v1/knowledge/数学/questions/by-struct",
            params={"struct_path": "必修一/第1章"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["has_doc_intel"] is True
    assert payload["hint"] == ""
    assert payload["questions"] == [
        {
            "node_id": "n-q1",
            "q_id": "P3-1",
            "text": "1. 计算 3 + 5 的值。",
            "question_type": "计算题",
            "difficulty": "",
            "struct_path": "必修一/第1章 集合/1.1 集合的概念",
            "has_answer": False,
            "file_name": "数学必修一_教师版.pdf",
        }
    ]


def test_questions_by_struct_prefix_does_not_leak_into_longer_chapter_numbers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """“第1章” must match 第1章… but never 第11章… (segment-aware prefix)."""
    with _questions_client(monkeypatch, _teacher_edition_docstore()) as client:
        response = client.get(
            "/api/v1/knowledge/数学/questions/by-struct",
            params={"struct_path": "第1章"},
        )

    assert response.status_code == 200
    q_ids = [q["q_id"] for q in response.json()["questions"]]
    assert "P5-11" not in q_ids


def test_questions_by_struct_matches_full_book_path(monkeypatch: pytest.MonkeyPatch) -> None:
    with _questions_client(monkeypatch, _teacher_edition_docstore()) as client:
        response = client.get(
            "/api/v1/knowledge/数学/questions/by-struct",
            params={"struct_path": "必修二/第11章 统计"},
        )

    assert response.status_code == 200
    questions = response.json()["questions"]
    assert [q["q_id"] for q in questions] == ["P5-11"]


def test_questions_by_struct_requires_nonempty_struct_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _questions_client(monkeypatch, _teacher_edition_docstore()) as client:
        response = client.get(
            "/api/v1/knowledge/数学/questions/by-struct",
            params={},
        )

    assert response.status_code == 422


def test_questions_by_struct_returns_empty_with_hint_when_no_doc_intel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A KB whose nodes carry no is_question/q_id markers fails open with a hint."""
    docstore = _docstore(
        _Node("n-plain", text="普通讲义内容", metadata={"file_name": "讲义.pdf"})
    )
    with _questions_client(monkeypatch, docstore) as client:
        response = client.get(
            "/api/v1/knowledge/讲义库/questions/by-struct",
            params={"struct_path": "第1章"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["questions"] == []
    assert payload["has_doc_intel"] is False
    assert "doc_intel" in payload["hint"]


def test_questions_by_struct_returns_empty_with_hint_for_empty_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _questions_client_without_docstore(monkeypatch) as client:
        response = client.get(
            "/api/v1/knowledge/空库/questions/by-struct",
            params={"struct_path": "第1章"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["questions"] == []
    assert payload["has_doc_intel"] is False
    assert payload["hint"]


# ── POST /exam-paper/assemble ──────────────────────────────────────────────


def test_assemble_generates_docx_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    with _assemble_client(monkeypatch, _teacher_edition_docstore()) as client:
        response = client.post(
            "/api/v1/exam-paper/assemble",
            json={
                "kb_name": "数学",
                "q_ids": ["n-q1", "n-q2"],
                "title": "第一章单元测试",
                "include_answers": False,
            },
        )

    assert response.status_code == 200
    assert (
        response.headers["content-type"]
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    # Non-ASCII title rides in the RFC 5987 filename*= part (the plain
    # filename= must stay latin-1 safe).
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "filename*=UTF-8''" in disposition
    assert "第一章单元测试.docx" in __import__("urllib.parse").parse.unquote(disposition)
    body = response.content
    assert body
    assert body[:2] == b"PK"  # docx is a ZIP container


def test_assemble_paper_content_contains_stems_and_answer_sheet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from docx import Document

    with _assemble_client(
        monkeypatch, _teacher_edition_docstore(), role="teacher"
    ) as client:
        response = client.post(
            "/api/v1/exam-paper/assemble",
            json={
                "kb_name": "数学",
                "q_ids": ["n-q1", "n-q2"],
                "include_answers": True,
            },
        )

    assert response.status_code == 200
    assert response.content[:2] == b"PK"
    doc = Document(BytesIO(response.content))
    texts = [p.text for p in doc.paragraphs if p.text.strip()]
    assert "共 2 题" in texts
    # The paper re-numbers questions, so the qa_split "1."/"2." stems are
    # stripped and re-prefixed by the renderer.
    assert "1. 计算 3 + 5 的值。" in texts
    assert "2. 判断下列说法是否正确。" in texts
    assert "参考答案" in texts
    assert "1. 答案：8" in texts


def test_assemble_resolves_q_id_metadata_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Raw ``q_id`` values (e.g. P3-1) work even though the frontend sends node ids."""
    with _assemble_client(monkeypatch, _teacher_edition_docstore()) as client:
        response = client.post(
            "/api/v1/exam-paper/assemble",
            json={"kb_name": "数学", "q_ids": ["P3-1"]},
        )

    assert response.status_code == 200
    assert response.content[:2] == b"PK"


def test_assemble_skips_missing_ids_with_warning_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-open: unknown ids are reported, found questions still export."""
    with _assemble_client(monkeypatch, _teacher_edition_docstore()) as client:
        response = client.post(
            "/api/v1/exam-paper/assemble",
            json={"kb_name": "数学", "q_ids": ["n-q1", "nope-1", "nope-2"]},
        )

    assert response.status_code == 200
    assert response.content[:2] == b"PK"
    assert response.headers.get("x-missing-ids") == "nope-1,nope-2"


def test_assemble_uses_inline_answer_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """An answer embedded on the question node (answer/correct_answer) is used
    even when no standalone is_answer block exists."""
    from docx import Document

    docstore = _docstore(
        _Node(
            "n-q1",
            text="1. 2x = 4，求 x。",
            metadata={
                "file_name": "练习册.pdf",
                "struct_path": "第1章",
                "is_question": True,
                "q_id": "P2-1",
                "answer": "x = 2",
            },
        )
    )
    with _assemble_client(monkeypatch, docstore) as client:
        response = client.post(
            "/api/v1/exam-paper/assemble",
            json={"kb_name": "数学", "q_ids": ["n-q1"], "include_answers": True},
        )

    assert response.status_code == 200
    doc = Document(BytesIO(response.content))
    texts = [p.text for p in doc.paragraphs if p.text.strip()]
    assert "1. x = 2" in texts


def test_assemble_404_when_no_question_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    with _assemble_client(monkeypatch, _teacher_edition_docstore()) as client:
        response = client.post(
            "/api/v1/exam-paper/assemble",
            json={"kb_name": "数学", "q_ids": ["nope-1"]},
        )

    assert response.status_code == 404


def test_assemble_404_for_empty_docstore(monkeypatch: pytest.MonkeyPatch) -> None:
    with _assemble_client(monkeypatch, None) as client:
        response = client.post(
            "/api/v1/exam-paper/assemble",
            json={"kb_name": "空库", "q_ids": ["n-q1"]},
        )

    assert response.status_code == 404


def test_assemble_answers_require_teacher_role(monkeypatch: pytest.MonkeyPatch) -> None:
    """Students may export a paper but never an answer sheet (mirrors paper-reorder)."""
    with _assemble_client(
        monkeypatch, _teacher_edition_docstore(), role="student"
    ) as client:
        response = client.post(
            "/api/v1/exam-paper/assemble",
            json={"kb_name": "数学", "q_ids": ["n-q1"], "include_answers": True},
        )

    assert response.status_code == 403
    assert "Teacher access is required" in response.json()["detail"]

    # Without answers the same student request succeeds.
    with _assemble_client(
        monkeypatch, _teacher_edition_docstore(), role="student"
    ) as client:
        ok_response = client.post(
            "/api/v1/exam-paper/assemble",
            json={"kb_name": "数学", "q_ids": ["n-q1"], "include_answers": False},
        )
    assert ok_response.status_code == 200


# ── route registration smoke ───────────────────────────────────────────────


def test_exam_paper_and_questions_routes_registered_in_main_app() -> None:
    api_main = importlib.import_module("deeptutor.api.main")
    # url_path_for resolves through the (possibly wrapped) included routers,
    # so a hit proves the router reached the assembled app with its prefix.
    assert (
        api_main.app.url_path_for("assemble_exam_paper")
        == "/api/v1/exam-paper/assemble"
    )
    assert (
        api_main.app.url_path_for("get_questions_by_struct", kb_name="数学")
        == "/api/v1/knowledge/数学/questions/by-struct"
    )
