"""Assignments router — 教师作业布置 + 学生作答 + 自动判分 + 教师统计 (B1 v1).

Storage is the JSON warehouse in :mod:`deeptutor.learning.assignments` (no new
database table). Question sources come verbatim from the KP-bound question
bank (``KP.meta["question_bank"]``) snapshot into each assignment at 布置
time, so a student always sees the question the teacher assigned even if the
bank rotates later.

Guards: teacher endpoints go through :func:`require_teacher` (role
``teacher``; single-user local runs pass as the implicit admin, the same
convention ``require_admin`` uses); student endpoints are plain
``require_auth`` — every record is scoped to the caller's own roster
membership / submissions, so nothing beyond authentication is needed.

v2 (B1-v2) snapshots both ``choice`` and ``short`` (数学填空) bank items: a
choice item keeps the letter-equality grading branch, a short item grades
through :func:`learning.grading.grade_answer` with ``question_type="short"``
(its math-normalization branch). On submit, wrong answers are linked back
into the *student's own* mastery store as ``error_records`` (only when the
student already has a LearningProgress for the assignment's book — the link
never creates a path), mirroring the record shape
:func:`LearningService.record_quiz_attempt` writes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from deeptutor.api.routers.auth import require_auth, require_teacher
from deeptutor.learning import assignments as store
from deeptutor.learning.grading import grade_answer
from deeptutor.learning.models import ErrorType, ErrorRecord, LearningProgress, RetryAttempt
from deeptutor.multi_user import classes as rosters
from deeptutor.services.auth import TokenPayload

logger = logging.getLogger(__name__)

router = APIRouter()

#: choice items only snapshot when their answer is a single option letter.
_CHOICE_ANSWER_RE = re.compile(r"^[A-Z]$")
#: short items only snapshot when their answer fits the fill-in budget.
_MAX_SHORT_ANSWER = 500
_MAX_KP_IDS = 50
_MAX_ANSWERS = 200


# ---------------------------------------------------------------------------
# Teacher-side question source: the teacher's own mastery store
# ---------------------------------------------------------------------------


def _teacher_learning_dir(user_id: str) -> Path:
    """Resolve one user's learning store directory (never creates it).

    Mirrors ``class_insights._student_learning_dir`` so the teacher reads
    their own per-user mastery workspace without switching the request's
    current user.
    """
    from deeptutor.multi_user.paths import USERS_ROOT
    from deeptutor.services.path_service import PathService

    scope_root = (USERS_ROOT / user_id).resolve()
    service = PathService(workspace_root=scope_root)
    return service.get_workspace_dir() / "learning"


def _load_teacher_book(user_id: str, book_id: str) -> LearningProgress | None:
    """Load one book's progress from the teacher's mastery store.

    The v2 store (``learning/mastery/mastery.sqlite3``) is the source of
    truth; the legacy per-book JSON file is the fallback, exactly like the
    class-insights aggregation. Fail-open: unreadable data returns ``None``
    rather than 500ing the assignment flow.
    """
    learning_dir = _teacher_learning_dir(user_id)
    if not learning_dir.is_dir():
        return None
    store_root = learning_dir / "mastery"
    if (store_root / "mastery.sqlite3").is_file():
        try:
            from deeptutor.learning.storage import LearningStore

            progress = LearningStore(root=store_root).load(book_id)
            if progress is not None:
                return progress
        except Exception as exc:  # noqa: BLE001 - a broken store degrades to legacy files
            logger.warning("Assignments: v2 store unreadable for %s: %s", user_id, exc)

    legacy = learning_dir / f"{book_id}.json"
    try:
        return LearningProgress.model_validate(json.loads(legacy.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def _first_question(kp: Any) -> dict[str, Any] | None:
    """The first snapshottable question bound to one KP, or ``None``.

    A bank item qualifies when it carries a question and is either a
    ``choice`` item whose answer is a single option letter, or a ``short``
    (数学填空) item whose answer is text within the fill-in budget.
    ``options`` are stored as the option bodies in their bound key order
    (``A`` first), so the student answers a choice item with the letter and
    grading compares letters; a short item carries no options and grades
    through the math-normalization branch of :func:`learning.grading`.
    """
    bank = (kp.meta or {}).get("question_bank") or []
    for item in bank:
        if not isinstance(item, dict):
            continue
        stem = str(item.get("question") or "").strip()
        if not stem:
            continue
        question_type = str(item.get("question_type") or "choice")
        if question_type == "choice":
            answer = str(item.get("answer") or "").strip().upper()
            if not _CHOICE_ANSWER_RE.fullmatch(answer):
                continue
            raw_options = item.get("options") or {}
            if isinstance(raw_options, dict):
                keys = sorted(raw_options)
                options = [str(raw_options[key]).strip() for key in keys]
            elif isinstance(raw_options, list):
                options = [str(body).strip() for body in raw_options]
            else:
                continue
            if not options or not all(options):
                continue
            question: dict[str, Any] = {
                "stem": stem,
                "options": options,
                "answer": answer,
                "type": "choice",
            }
        elif question_type == "short":
            answer = str(item.get("answer") or "").strip()
            if not answer or len(answer) > _MAX_SHORT_ANSWER:
                continue
            question = {"stem": stem, "options": [], "answer": answer, "type": "short"}
        else:
            continue
        explanation = str(item.get("explanation") or "").strip()
        if explanation:
            question["explanation"] = explanation
        return question
    return None


# ---------------------------------------------------------------------------
# Guards over the class rosters
# ---------------------------------------------------------------------------


def _require_class_owner(class_id: str, username: str) -> dict[str, Any]:
    """Load the class and enforce that ``username`` owns it (teacher flow)."""
    record = rosters.get_class(class_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Class not found.")
    if str(record.get("teacher") or "") != username:
        raise HTTPException(status_code=403, detail="You can only manage your own classes.")
    return record


def _roster_usernames(class_id: str) -> list[str]:
    """Live student usernames of one class (fail-open, read-time verified)."""
    record = rosters.get_class(class_id)
    if record is None:
        return []
    return rosters.resolve_roster_live(record, rosters.read_user_store())


# ---------------------------------------------------------------------------
# Response shaping
# ---------------------------------------------------------------------------


def _question_type(question: dict[str, Any]) -> str:
    """``"choice"`` or ``"short"`` (v1 snapshots predate the ``type`` key)."""
    value = str(question.get("type") or "choice")
    return value if value in ("choice", "short") else "choice"


def _public_question(question: dict[str, Any]) -> dict[str, Any]:
    """Student-facing question: the answer and explanation are stripped.

    ``type`` travels so the card knows to render a fill-in input; ``options``
    stays for shape stability and is empty for a short item.
    """
    return {
        "stem": question.get("stem") or "",
        "type": _question_type(question),
        "options": list(question.get("options") or []),
    }


def _public_result(result: dict[str, Any]) -> dict[str, Any]:
    """Student-facing result: correctness only, never any answer text.

    Even the student's *own* submitted answer is dropped from API responses —
    the student card only renders right/wrong, and a blanket no-``answer``
    guarantee over the whole student surface (设计验收判据: /mine 响应
    grep 不到 "answer") is cheaper to audit than a field-by-field one.
    """
    return {
        "kp_id": str(result.get("kp_id") or ""),
        "q_idx": result.get("q_idx"),
        "correct": bool(result.get("correct")),
    }


def _public_items(record: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for q_idx, item in enumerate(record.get("items") or []):
        items.append(
            {
                "kp_id": str(item.get("kp_id") or ""),
                "q_idx": q_idx,
                **_public_question(item.get("question") or {}),
            }
        )
    return items


def _assignment_summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "assignment_id": record.get("assignment_id"),
        "class_id": record.get("class_id"),
        "title": record.get("title") or "",
        "created_at": record.get("created_at"),
        "due_at": record.get("due_at"),
        "item_count": len(record.get("items") or []),
        "submission_count": len(record.get("submissions") or {}),
    }


def _find_item(record: dict[str, Any], q_idx: int) -> dict[str, Any] | None:
    items = record.get("items") or []
    if isinstance(q_idx, bool) or not isinstance(q_idx, int) or not 0 <= q_idx < len(items):
        return None
    return items[q_idx]


class AssignRequest(BaseModel):
    class_id: str = Field(min_length=1)
    book_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=200)
    kp_ids: list[str] = Field(min_length=1, max_length=_MAX_KP_IDS)
    due_at: str | None = Field(default=None, max_length=64)


@router.post("")
async def create_assignment(
    body: AssignRequest,
    current: TokenPayload = Depends(require_teacher),
) -> dict[str, Any]:
    """布置作业: snapshot the chosen KPs' first snapshottable question into items.

    KPs without a snapshottable question (no bank binding, other item type,
    non-letter choice answer, over-budget short answer) are skipped and
    reported in ``skipped`` — a partially covered selection still assigns the
    questions that do exist. ``book_id`` rides along on the record so the
    submit endpoint can link wrong answers into the student's own mastery
    store for that book.
    """
    username = str(getattr(current, "username", "") or "")
    user_id = str(getattr(current, "user_id", "") or username)
    _require_class_owner(body.class_id, username)

    progress = await asyncio.to_thread(_load_teacher_book, user_id, body.book_id)
    if progress is None:
        raise HTTPException(status_code=404, detail="Mastery progress not found")
    by_id = {
        kp.id: kp for module in progress.modules for kp in module.knowledge_points
    }

    items: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for kp_id in body.kp_ids:
        kp_id = str(kp_id or "").strip()
        if not kp_id or kp_id in seen:
            continue
        seen.add(kp_id)
        kp = by_id.get(kp_id)
        question = _first_question(kp) if kp is not None else None
        if question is None:
            skipped.append({"kp_id": kp_id, "reason": "no_question"})
            continue
        items.append({"kp_id": kp_id, "question": question})

    if not items:
        raise HTTPException(status_code=400, detail="No questions available for the selected KPs")

    record = {
        "assignment_id": store.new_assignment_id(),
        "class_id": body.class_id,
        "book_id": body.book_id.strip(),
        "teacher_id": username,
        "title": body.title.strip(),
        "created_at": store.utc_now_iso(),
        "due_at": (body.due_at or "").strip() or None,
        "items": items,
        "submissions": {},
    }
    saved = store.save_assignment(record)
    return {"assignment": _assignment_summary(saved), "skipped": skipped}


@router.get("")
async def list_teacher_assignments(
    class_id: str | None = None,
    current: TokenPayload = Depends(require_teacher),
) -> dict[str, Any]:
    """本班作业列表 + 每生每 KP 正确率聚合统计.

    Rostered students always appear (whitelist semantics, like the class
    insights view) with ``submitted: false`` until they hand in; per-KP
    accuracy is ``correct / total`` over that KP's items.
    """
    username = str(getattr(current, "username", "") or "")
    if class_id:
        _require_class_owner(class_id, username)
    roster_cache: dict[str, list[str]] = {}

    assignments: list[dict[str, Any]] = []
    for record in store.read_assignments().values():
        if not isinstance(record, dict) or str(record.get("teacher_id") or "") != username:
            continue
        record_class = str(record.get("class_id") or "")
        if class_id and record_class != class_id:
            continue
        if record_class not in roster_cache:
            roster_cache[record_class] = _roster_usernames(record_class)

        items = record.get("items") or []
        kp_totals: dict[str, int] = {}
        type_totals: dict[str, int] = {}
        for item in items:
            kp_totals[str(item.get("kp_id") or "")] = kp_totals.get(str(item.get("kp_id") or ""), 0) + 1
            item_type = _question_type(item.get("question") or {})
            type_totals[item_type] = type_totals.get(item_type, 0) + 1
        students: list[dict[str, Any]] = []
        submissions = record.get("submissions") or {}
        for student in roster_cache[record_class]:
            submission = submissions.get(student) if isinstance(submissions, dict) else None
            correct = 0
            per_kp: dict[str, int] = {}
            type_correct: dict[str, int] = {}
            if isinstance(submission, dict):
                for result in submission.get("results") or []:
                    if not isinstance(result, dict) or not result.get("correct"):
                        continue
                    correct += 1
                    kp_id = str(result.get("kp_id") or "")
                    per_kp[kp_id] = per_kp.get(kp_id, 0) + 1
                    q_idx = result.get("q_idx")
                    result_type = _question_type(
                        (items[q_idx].get("question") or {})
                        if isinstance(q_idx, int)
                        and not isinstance(q_idx, bool)
                        and 0 <= q_idx < len(items)
                        else {}
                    )
                    type_correct[result_type] = type_correct.get(result_type, 0) + 1
            students.append(
                {
                    "username": student,
                    "submitted": isinstance(submission, dict),
                    "submitted_at": submission.get("submitted_at") if isinstance(submission, dict) else None,
                    "correct": correct,
                    "total": len(items),
                    "per_kp": [
                        {
                            "kp_id": kp_id,
                            "correct": per_kp.get(kp_id, 0),
                            "total": kp_totals.get(kp_id, 0),
                        }
                        for kp_id in kp_totals
                    ],
                    "per_type": [
                        {
                            "type": item_type,
                            "correct": type_correct.get(item_type, 0),
                            "total": type_totals.get(item_type, 0),
                        }
                        for item_type in sorted(type_totals)
                    ],
                }
            )
        entry = _assignment_summary(record)
        entry["students"] = students
        assignments.append(entry)
    return {"assignments": assignments}


@router.get("/kps")
async def list_kp_questions(
    book_id: str,
    current: TokenPayload = Depends(require_teacher),
) -> dict[str, Any]:
    """The teacher's own KPs for one book, with a question preview.

    Feeds the teacher page's KP multi-select: ``preview`` carries the stem and
    option bodies only (never the answer), so a KP without an assignable
    question still shows up — with ``has_question: false`` — instead of
    silently vanishing from the picker. A short-item preview carries no
    options (the card renders a fill-in input instead).
    """
    username = str(getattr(current, "username", "") or "")
    user_id = str(getattr(current, "user_id", "") or username)
    progress = await asyncio.to_thread(_load_teacher_book, user_id, book_id)
    if progress is None:
        raise HTTPException(status_code=404, detail="Mastery progress not found")
    kps: list[dict[str, Any]] = []
    for module in progress.modules:
        for kp in module.knowledge_points:
            question = _first_question(kp)
            preview = _public_question(question) if question else None
            kps.append(
                {
                    "kp_id": kp.id,
                    "name": kp.name,
                    "module": module.name,
                    "has_question": question is not None,
                    "preview": preview,
                }
            )
    return {"book_id": book_id, "kps": kps}


@router.get("/mine")
async def list_my_assignments(
    payload: TokenPayload = Depends(require_auth),
) -> dict[str, Any]:
    """学生视角: the assignments of every class they are rostered into.

    Items come back with the answer and explanation stripped (防答案泄漏);
    a handed-in assignment adds the student's own ``results`` — their answers
    and the per-item correct flags, never the expected answer.
    """
    username = str(getattr(payload, "username", "") or "")
    results: list[dict[str, Any]] = []
    for record in store.read_assignments().values():
        if not isinstance(record, dict):
            continue
        if username not in _roster_usernames(str(record.get("class_id") or "")):
            continue
        entry: dict[str, Any] = {
            "assignment_id": record.get("assignment_id"),
            "class_id": record.get("class_id"),
            "title": record.get("title") or "",
            "created_at": record.get("created_at"),
            "due_at": record.get("due_at"),
            "items": _public_items(record),
        }
        submission = (record.get("submissions") or {}).get(username)
        if isinstance(submission, dict):
            entry["status"] = "done"
            entry["submitted_at"] = submission.get("submitted_at")
            entry["results"] = [
                _public_result(result)
                for result in submission.get("results") or []
                if isinstance(result, dict)
            ]
        else:
            entry["status"] = "todo"
        results.append(entry)
    return {"assignments": results}


class SubmitRequest(BaseModel):
    answers: list[dict[str, Any]] = Field(default_factory=list, max_length=_MAX_ANSWERS)


def _assignment_question_id(assignment_id: str, q_idx: int) -> str:
    """The error-record question id for one assignment item.

    Encodes the whole attribution in the field the existing error-record
    lifecycle already dedupes on (``question_id`` + ``knowledge_point_id``):
    ``asg:`` marks the source, the rest carries the assignment and the item
    index, so a resubmission can never mint a second record for the same
    question.
    """
    return f"asg:{assignment_id}:{q_idx}"


def _link_assignment_errors(record: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    """Mirror wrong answers into the *caller's own* mastery store.

    Runs under ``require_auth``, so :class:`LearningStore` resolves to the
    submitting student's workspace — no cross-user write is possible. The
    record shape and status semantics mirror
    :func:`LearningService.record_quiz_attempt`: a wrong answer appends an
    ``active`` :class:`ErrorRecord` (``application``), a later correct
    submission graduates the existing one, and an already-known question is
    left untouched (idempotent — first error stands). Only an *existing*
    LearningProgress for the assignment's book participates: nothing is
    created, and the skip is reported back.
    """
    assignment_id = str(record.get("assignment_id") or "")
    book_id = str(record.get("book_id") or "").strip()
    if not book_id:
        return {"linked": 0, "skipped": "no_book"}
    if not results:
        return {"linked": 0, "skipped": None}

    try:
        from deeptutor.services.path_service import get_path_service

        learning_dir = get_path_service().get_workspace_dir() / "learning"
        if not learning_dir.is_dir():
            # 学生从未进入过学习空间 —— 不为其凭空建 store,跳过并说明。
            return {"linked": 0, "skipped": "no_progress"}

        from deeptutor.learning.storage import LearningStore

        mastery = LearningStore()
    except Exception as exc:  # noqa: BLE001 - the submission itself must not fail
        logger.warning("Assignments: mastery store unavailable for error link: %s", exc)
        return {"linked": 0, "skipped": "store_unavailable"}

    try:
        progress = mastery.load(book_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Assignments: progress %s unreadable for error link: %s", book_id, exc)
        return {"linked": 0, "skipped": "progress_unreadable"}
    if progress is None:
        # 学生还没有这本书的路径 —— 不自动创建,跳过并说明。
        return {"linked": 0, "skipped": "no_progress"}
    module_by_kp = {
        kp.id: module.id for module in progress.modules for kp in module.knowledge_points
    }

    def apply(tx: Any) -> int:
        linked = 0
        for result in results:
            question_id = _assignment_question_id(assignment_id, result["q_idx"])
            kp_id = result["kp_id"]
            existing = next(
                (
                    rec
                    for rec in tx.progress.error_records
                    if rec.question_id == question_id and rec.knowledge_point_id == kp_id
                ),
                None,
            )
            if result["correct"]:
                # A resubmission that fixes the answer graduates the open
                # record, exactly like a correct mastery-quiz retry does.
                if existing is not None and existing.status in ("active", "retrying"):
                    existing.retry_history.append(
                        RetryAttempt(
                            timestamp=time.time(),
                            is_correct=True,
                            attempt_number=len(existing.retry_history) + 1,
                        )
                    )
                    existing.status = "graduated"
                    tx.touch()
                continue
            if existing is not None:
                continue
            tx.progress.error_records.append(
                ErrorRecord(
                    id=uuid4().hex,
                    question_id=question_id,
                    knowledge_point_id=kp_id,
                    module_id=module_by_kp.get(kp_id, ""),
                    error_type=ErrorType.APPLICATION_ERROR,
                    self_attribution=f"assignment:{assignment_id}",
                    status="active",
                )
            )
            linked += 1
        if linked:
            tx.touch()
        return linked

    try:
        linked = mastery.mutate(book_id, apply, create=False)[1]
    except KeyError:
        return {"linked": 0, "skipped": "no_progress"}
    except Exception as exc:  # noqa: BLE001 - the submission itself must not fail
        logger.warning("Assignments: error link failed for %s/%s: %s", book_id, assignment_id, exc)
        return {"linked": 0, "skipped": "link_failed"}
    return {"linked": int(linked), "skipped": None}


@router.post("/{assignment_id}/submit")
async def submit_assignment(
    assignment_id: str,
    body: SubmitRequest,
    payload: TokenPayload = Depends(require_auth),
) -> dict[str, Any]:
    """学生提交: grade every item via ``learning.grading`` and store the attempt.

    choice items grade through the letter-equality branch, short (数学填空)
    items through the math-normalization branch of :func:`learning.grading`
    — the router never reimplements the comparison. An item the student left
    out is recorded as an unanswered wrong answer so the teacher's stats see
    the full paper. Resubmitting replaces the previous attempt (last attempt
    wins). The response returns per-item correct flags only — the expected
    answer never leaves the server — plus the error-link report.
    """
    username = str(getattr(payload, "username", "") or "")
    record = await asyncio.to_thread(store.get_assignment, assignment_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Assignment not found")
    if username not in _roster_usernames(str(record.get("class_id") or "")):
        raise HTTPException(status_code=403, detail="You are not in this class.")

    submitted: dict[int, str] = {}
    for entry in body.answers:
        if not isinstance(entry, dict):
            continue
        q_idx = entry.get("q_idx")
        if isinstance(q_idx, bool) or not isinstance(q_idx, int):
            continue
        submitted[q_idx] = str(entry.get("answer") or "").strip()

    results: list[dict[str, Any]] = []
    for q_idx, item in enumerate(record.get("items") or []):
        question = item.get("question") or {}
        question_type = _question_type(question)
        expected = str(question.get("answer") or "")
        answer = submitted.get(q_idx, "")
        if question_type == "choice":
            answer = answer.upper()[:200]
        else:
            answer = answer[:_MAX_SHORT_ANSWER]
        correct = bool(answer) and bool(expected) and grade_answer(answer, expected, question_type)
        results.append(
            {
                "kp_id": str(item.get("kp_id") or ""),
                "q_idx": q_idx,
                "answer": answer,
                "correct": correct,
            }
        )

    try:
        updated = await asyncio.to_thread(
            store.add_submission, assignment_id, username, results
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Assignment not found")

    error_link = await asyncio.to_thread(_link_assignment_errors, record, results)
    correct_count = sum(1 for result in results if result["correct"])
    return {
        "assignment_id": assignment_id,
        "correct_count": correct_count,
        "total": len(results),
        "results": [_public_result(result) for result in results],
        "error_link": error_link,
    }
