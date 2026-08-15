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

from fastapi import APIRouter, Depends

from deeptutor.api.routers.auth import require_admin_or_teacher
from deeptutor.learning.models import LearningProgress
from deeptutor.learning.policy import display_mastery, is_mastered
from deeptutor.services.auth import TokenPayload

logger = logging.getLogger(__name__)

router = APIRouter()

#: Display mastery is rounded to this many decimals in the weak-KP list.
_WEAK_MASTERY_ROUND = 3


def _read_user_store() -> dict:
    """Read the account store as JSON without writing anything.

    Prefers the canonical ``data/system/auth/users.json``; falls back to the
    legacy ``data/user/auth_users.json``. Malformed stores log and return
    empty — fail-open, never raising.
    """
    from deeptutor.multi_user.identity import LEGACY_USERS_FILE, USERS_FILE

    for path in (USERS_FILE, LEGACY_USERS_FILE):
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Class insights: unreadable user store %s: %s", path, exc)
            continue
        if isinstance(loaded, dict):
            return loaded
        logger.warning("Class insights: user store %s is not a JSON object", path)
    return {}


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
    _: TokenPayload = Depends(require_admin_or_teacher),
) -> dict:
    """Return the per-student mastery aggregation for all student accounts.

    Students are ordered by average mastery (weakest first) so the class's
    at-risk learners surface at the top. ``generated_at`` is the server-side
    ISO timestamp of the snapshot.
    """
    students: list[dict] = []
    for username, user_id in _student_accounts():
        try:
            student = _aggregate_student(username, user_id)
        except Exception as exc:  # noqa: BLE001 - one broken student must not kill the class
            logger.warning("Class insights: skipped student %r: %s", username, exc)
            continue
        if student is not None:
            students.append(student)

    students.sort(key=lambda student: (student["avg_mastery_pct"], student["username"]))
    return {
        "students": students,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
