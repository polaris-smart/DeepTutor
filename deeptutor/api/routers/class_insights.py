"""Class insights router — admin/teacher overview of student learning.

``GET /api/v1/class-insights/overview`` aggregates mastery for every
``role=student`` account. Per-student progress is read from each student's
own per-user workspace (``data/users/<uid>/user/workspace/learning/*.json``,
the same root ``LearningStore`` uses for a per-user request), so the numbers
honour the multi-user path-service isolation without ever switching the
request's current user.

Read-only by design: the account store is read straight off disk with
``json.loads`` (bypassing the canonicalizing loader in
``multi_user.identity``, which may rewrite non-canonical records), and no
workspace directory is created. Fail-open: a student whose learning data is
missing or unreadable is skipped, and a corrupt book file is skipped while
the student's remaining books still aggregate — the request never 500s
because one student's data is broken.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from deeptutor.api.routers.auth import require_admin_or_teacher, require_auth
from deeptutor.learning.models import LearningProgress
from deeptutor.learning.policy import display_mastery, is_mastered
from deeptutor.multi_user import classes as rosters
from deeptutor.services.auth import TokenPayload

logger = logging.getLogger(__name__)

router = APIRouter()

#: Display mastery is rounded to this many decimals in the weak-KP list.
_WEAK_MASTERY_ROUND = 3


def _read_user_store() -> dict:
    """Raw fail-open account-store read (single implementation lives in
    :mod:`deeptutor.multi_user.classes` so rosters and insights can never
    drift apart on how records are read)."""
    return rosters.read_user_store()


def _student_accounts() -> list[tuple[str, str]]:
    """Return ``(username, user_id)`` pairs for every ``role=student`` account.

    The user id is what the multi-user layer keys per-user workspaces by
    (``data/users/<uid>``); a record without one falls back to the username,
    mirroring ``user_from_token_payload``.
    """
    accounts: list[tuple[str, str]] = []
    for username, value in _read_user_store().items():
        if not isinstance(value, dict):
            continue
        role = str(value.get("role") or "user")
        if role != "student":
            continue
        user_id = str(value.get("id") or username or "")
        if not user_id:
            continue
        accounts.append((str(username), user_id))
    return accounts


def _children_accounts(parent_username: str, store: dict) -> list[tuple[str, str]]:
    """``(username, user_id)`` pairs for a parent record's ``children`` list.

    Only entries that resolve to an existing ``role=student`` account count —
    a stale child name (account deleted) is silently skipped, fail-open like
    the rest of this module.
    """
    record = store.get(parent_username) or {}
    children = record.get("children") if isinstance(record, dict) else None
    if not isinstance(children, list):
        return []
    accounts: list[tuple[str, str]] = []
    for child in children:
        name = str(child or "").strip()
        if not name:
            continue
        value = store.get(name)
        if not isinstance(value, dict):
            continue
        if str(value.get("role") or "") != "student":
            continue
        accounts.append((name, str(value.get("id") or name)))
    return accounts


def _student_learning_dir(user_id: str) -> Path:
    """Resolve one student's learning store directory (never creates it)."""
    from deeptutor.multi_user.paths import USERS_ROOT
    from deeptutor.services.path_service import PathService

    scope_root = (USERS_ROOT / user_id).resolve()
    service = PathService(workspace_root=scope_root)
    return service.get_workspace_dir() / "learning"


def _load_student_books(user_id: str) -> list[LearningProgress]:
    """Load every readable :class:`LearningProgress` for one student.

    A missing directory yields ``[]``; a corrupt book file is logged and
    skipped so the student's remaining books still count (fail-open).
    """
    learning_dir = _student_learning_dir(user_id)
    if not learning_dir.is_dir():
        return []
    books: list[LearningProgress] = []
    for path in sorted(learning_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            books.append(LearningProgress.model_validate(data))
        except Exception as exc:  # noqa: BLE001 - any corrupt file is one book, not the class
            logger.warning("Class insights: skipped unreadable progress %s: %s", path, exc)
    return books


def _aggregate_student(username: str, user_id: str) -> dict | None:
    """Aggregate one student's mastery across all books.

    Returns ``None`` when the student has no readable learning data (skipped
    by the endpoint — fail-open). ``weak`` lists every knowledge point whose
    displayed mastery is below its type's gate threshold, weakest first.
    """
    books = _load_student_books(user_id)
    if not books:
        return None

    kp_total = 0
    mastery_sum = 0.0
    weak: list[dict] = []
    last_active: float | None = None
    for progress in books:
        kps = [kp for module in progress.modules for kp in module.knowledge_points]
        kp_total += len(kps)
        for kp in kps:
            mastery = float(display_mastery(progress, kp))
            mastery_sum += mastery
            if not is_mastered(progress, kp):
                weak.append(
                    {
                        "kp_id": kp.id,
                        "name": kp.name,
                        "mastery": round(mastery, _WEAK_MASTERY_ROUND),
                    }
                )
        if progress.updated_at:
            last_active = max(last_active or 0.0, float(progress.updated_at))

    weak.sort(key=lambda item: (item["mastery"], item["kp_id"]))
    return {
        "username": username,
        "kp_total": kp_total,
        "avg_mastery_pct": round(mastery_sum / kp_total * 100) if kp_total else 0,
        "weak": weak,
        "last_active": (
            datetime.fromtimestamp(last_active, tz=timezone.utc).isoformat()
            if last_active is not None
            else None
        ),
    }


@router.get("/overview")
async def class_overview(
    current: TokenPayload = Depends(require_admin_or_teacher),
    class_id: str | None = None,
) -> dict:
    """Return the per-student mastery aggregation for all student accounts.

    Students are ordered by average mastery (weakest first) so the class's
    at-risk learners surface at the top. ``generated_at`` is the server-side
    ISO timestamp of the snapshot.

    ``class_id`` narrows the view to one roster (K12 班级). A filtered view
    uses whitelist semantics: every rostered student appears, including a
    ``no_data`` placeholder when they have no learning data yet — a missing
    roster member is an anomaly signal, while the unfiltered flat view keeps
    skipping dataless students. Teachers may only filter by their own class.
    """
    class_record: dict | None = None
    if class_id:
        role = str(getattr(current, "role", "") or "")
        username = str(getattr(current, "username", "") or "")
        class_record = rosters.get_class(class_id)
        if class_record is None:
            raise HTTPException(status_code=404, detail="Class not found.")
        if role != "admin" and str(class_record.get("teacher") or "") != username:
            raise HTTPException(
                status_code=403, detail="You can only view your own classes."
            )
        store = _read_user_store()
        targets = [
            (name, str(store.get(name, {}).get("id") or name))
            for name in rosters.resolve_roster_live(class_record, store)
        ]
    else:
        targets = _student_accounts()

    students: list[dict] = []
    for username, user_id in targets:
        try:
            student = _aggregate_student(username, user_id)
        except Exception as exc:  # noqa: BLE001 - one broken student must not kill the class
            logger.warning("Class insights: skipped student %r: %s", username, exc)
            continue
        if student is None and class_record is not None:
            # 班级视图白名单语义：花名册在册但还没有学习数据的学生也要出现，
            # 与 /my-children 的家庭视图一致；平铺视图跳过无数据学生不变。
            student = {
                "username": username,
                "kp_total": 0,
                "avg_mastery_pct": 0,
                "weak": [],
                "last_active": None,
                "no_data": True,
            }
        if student is not None:
            students.append(student)

    students.sort(key=lambda student: (student["avg_mastery_pct"], student["username"]))
    response: dict = {
        "students": students,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if class_record is not None:
        response["class"] = {
            "id": class_id,
            "name": str(class_record.get("name") or ""),
        }
    return response


class ClassCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=rosters.MAX_NAME_CHARS)
    students: list[str] = Field(default_factory=list, max_length=rosters.MAX_STUDENTS)


class ClassUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=rosters.MAX_NAME_CHARS)
    students: list[str] | None = Field(default=None, max_length=rosters.MAX_STUDENTS)


def _roster_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, rosters.ClassNotFoundError):
        return HTTPException(status_code=404, detail="Class not found.")
    if isinstance(exc, rosters.ClassForbiddenError):
        return HTTPException(status_code=403, detail="You can only manage your own classes.")
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/classes")
async def list_classes(
    current: TokenPayload = Depends(require_admin_or_teacher),
) -> dict:
    """List class rosters — a teacher sees their own, an admin sees all."""
    role = str(getattr(current, "role", "") or "")
    username = str(getattr(current, "username", "") or "")
    teacher = None if role == "admin" else username
    return {"classes": rosters.list_classes(teacher=teacher)}


@router.post("/classes")
async def create_class(
    body: ClassCreateRequest,
    current: TokenPayload = Depends(require_admin_or_teacher),
) -> dict:
    """Create a class roster owned by the current teacher (or admin)."""
    username = str(getattr(current, "username", "") or "")
    try:
        record = rosters.create_class(
            name=body.name, teacher=username, students=body.students
        )
    except rosters.ClassRosterError as exc:
        raise _roster_http_error(exc) from exc
    return {"class": record}


@router.put("/classes/{class_id}")
async def update_class(
    class_id: str,
    body: ClassUpdateRequest,
    current: TokenPayload = Depends(require_admin_or_teacher),
) -> dict:
    """Rename a class and/or replace its roster (owner or admin)."""
    role = str(getattr(current, "role", "") or "")
    username = str(getattr(current, "username", "") or "")
    try:
        record = rosters.update_class(
            class_id,
            caller=username,
            is_admin=role == "admin",
            name=body.name,
            students=body.students,
        )
    except (rosters.ClassRosterError, rosters.ClassNotFoundError, rosters.ClassForbiddenError) as exc:
        raise _roster_http_error(exc) from exc
    return {"class": record}


@router.delete("/classes/{class_id}")
async def delete_class(
    class_id: str,
    current: TokenPayload = Depends(require_admin_or_teacher),
) -> dict:
    """Delete a class roster (owner or admin). Accounts are never touched."""
    role = str(getattr(current, "role", "") or "")
    username = str(getattr(current, "username", "") or "")
    try:
        rosters.delete_class(class_id, caller=username, is_admin=role == "admin")
    except (rosters.ClassNotFoundError, rosters.ClassForbiddenError) as exc:
        raise _roster_http_error(exc) from exc
    return {"ok": True}


@router.get("/my-children")
async def my_children_overview(
    payload: TokenPayload = Depends(require_auth),
) -> dict:
    """Parent view of learning mastery, restricted to their own children.

    ``children`` is the username list stored on the parent account record
    (family linkage). Admins get the same shape over all students so ops
    tooling can reuse the endpoint. Teachers/students are rejected — they
    have :ref:`/overview` and their own views respectively.
    """
    role = str(getattr(payload, "role", "") or "")
    username = str(getattr(payload, "username", "") or "")
    if role not in ("parent", "admin"):
        raise HTTPException(
            status_code=403,
            detail="Parent insights require the parent (or admin) role.",
        )
    store = _read_user_store()
    targets = _student_accounts() if role == "admin" else _children_accounts(username, store)

    students: list[dict] = []
    for child_username, child_user_id in targets:
        try:
            student = _aggregate_student(child_username, child_user_id)
        except Exception as exc:  # noqa: BLE001 - one broken child must not kill the view
            logger.warning("Parent insights: skipped child %r: %s", child_username, exc)
            continue
        if student is None:
            # 家长视角完整性：孩子账号存在但还没有学习数据时也要出现，
            # 否则家长会以为账号坏了（class 视图跳过无数据学生是合理的，
            # 家庭视图的孩子是白名单，缺员即异常信号）。
            student = {
                "username": child_username,
                "kp_total": 0,
                "avg_mastery_pct": 0,
                "weak": [],
                "last_active": None,
                "no_data": True,
            }
        students.append(student)

    students.sort(key=lambda student: (student["avg_mastery_pct"], student["username"]))
    return {
        "students": students,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
