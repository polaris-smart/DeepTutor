"""Public, stable views of pending mastery questions.

The persisted :class:`~deeptutor.learning.models.PendingQuestion` contains the
server-only expected answer. This module projects it into the smaller contract
that is safe to give to the tutor model and interactive clients. It also owns
the pure multiple-choice translations shared by registration, presentation,
and grading, so all three boundaries use the same immutable label/body map.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from deeptutor.learning.models import PendingQuestion


OPTION_PREFIX_RE = re.compile(r"^\s*([A-Z])\s*[.:：、)）-]\s*(.+)$", re.IGNORECASE)


def parse_options(options: list[str]) -> dict[str, str]:
    """Map persisted option strings to their stable ``{label: body}`` form."""
    result: dict[str, str] = {}
    for idx, raw in enumerate(options):
        text = str(raw or "").strip()
        if not text:
            continue
        match = OPTION_PREFIX_RE.match(text)
        if match:
            result[match.group(1).upper()] = match.group(2).strip()
        elif len(text) == 1 and text.isalnum():
            result[text.upper()] = text
        else:
            result[chr(ord("A") + idx) if idx < 26 else str(idx + 1)] = text
    return result


def has_option_bodies(options: dict[str, str]) -> bool:
    """Whether a choice map holds real answer text, not only A/B/C labels."""
    return len(options) >= 2 and all(
        value.strip() and value.strip().upper() != key.upper() for key, value in options.items()
    )


def format_options(options: dict[str, str]) -> list[str]:
    """Render a choice map as canonical, persistable ``"label: body"`` strings."""
    return [f"{label}: {body}" for label, body in options.items()]


def resolve_answer(answer: str, options: dict[str, str]) -> str:
    """Resolve a label, labelled option, or unique body to its stable label."""
    candidate = str(answer or "").strip()
    if not candidate:
        return ""

    key = candidate.upper()
    if key in options:
        return key

    prefix_match = OPTION_PREFIX_RE.match(candidate)
    if prefix_match and prefix_match.group(1).upper() in options:
        return prefix_match.group(1).upper()

    needle = candidate.casefold()
    exact = [label for label, text in options.items() if text.casefold() == needle]
    if len(exact) == 1:
        return exact[0]
    contained = [label for label, text in options.items() if needle in text.casefold()]
    return contained[0] if len(contained) == 1 else ""


def resolve_choice_submission(answer: str, options: dict[str, str]) -> str:
    """Resolve a learner submission by label or one exact, unique option body.

    Registration remains forgiving of a model-supplied body fragment through
    :func:`resolve_answer`; grading is intentionally stricter so a partial word
    cannot accidentally count as a correct learner answer.
    """
    candidate = str(answer or "").strip()
    if not candidate:
        return ""
    key = candidate.upper()
    if key in options:
        return key
    prefix_match = OPTION_PREFIX_RE.match(candidate)
    if prefix_match and prefix_match.group(1).upper() in options:
        return prefix_match.group(1).upper()
    needle = candidate.casefold()
    exact = [label for label, body in options.items() if body.casefold() == needle]
    return exact[0] if len(exact) == 1 else ""


@dataclass(frozen=True, slots=True)
class PublicPendingOption:
    """One learner-visible option; ``id`` and ``label`` are intentionally stable."""

    id: str
    label: str
    body: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "label": self.label, "body": self.body}

    def to_ask_user_dict(self) -> dict[str, str]:
        return {"label": self.label, "description": self.body}


@dataclass(frozen=True, slots=True)
class PublicPendingQuestion:
    """Learner-visible pending state, deliberately excluding the answer key."""

    question_id: str
    prompt: str
    question_type: str
    options: tuple[PublicPendingOption, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "prompt": self.prompt,
            "question_type": self.question_type,
            "options": [option.to_dict() for option in self.options],
        }

    def to_ask_user_dict(self) -> dict[str, Any]:
        return {
            "id": self.question_id,
            "prompt": self.prompt,
            "options": [option.to_ask_user_dict() for option in self.options],
            "multi_select": False,
            "allow_free_text": True,
        }


def public_pending_question(pending: PendingQuestion) -> PublicPendingQuestion:
    """Project persisted pending state without exposing ``expected_answer``."""
    choice_map = parse_options(list(pending.options or []))
    options = (
        tuple(
            PublicPendingOption(id=label, label=label, body=body)
            for label, body in choice_map.items()
        )
        if pending.question_type == "choice"
        else ()
    )
    return PublicPendingQuestion(
        question_id=pending.question_id,
        prompt=pending.prompt,
        question_type=pending.question_type,
        options=options,
    )


#: Id suffix for the confidence sub-question on a mastery question card.
CONFIDENCE_QUESTION_ID_SUFFIX = "_conf"
#: Fixed prompt for the confidence sub-question (教研审计 confidence_before
#: 采集). One shared prompt keeps the zh/en cards byte-identical, so the value
#: the tutor reads back maps to the same 1-5 scale everywhere.
CONFIDENCE_QUESTION_PROMPT = "你有多大把握？(1=纯猜, 5=非常确定)"
CONFIDENCE_OPTIONS: tuple[dict[str, str | None], ...] = tuple(
    {"label": str(i), "description": None} for i in range(1, 6)
)


def confidence_ask_user_question(question_id: str) -> dict[str, Any]:
    """The Likert sub-question appended to one mastery question's card.

    id is the main question id plus ``_conf``; options are the fixed 1-5
    scale. Free text stays off so the answer is a clean integer the tutor can
    pass straight back as ``mastery_grade.confidence_before``.
    """
    return {
        "id": f"{question_id}{CONFIDENCE_QUESTION_ID_SUFFIX}",
        "prompt": CONFIDENCE_QUESTION_PROMPT,
        "options": list(CONFIDENCE_OPTIONS),
        "multi_select": False,
        "allow_free_text": False,
    }


def pending_ask_user_questions(pending: PendingQuestion) -> list[dict[str, Any]]:
    """The full two-tab card for a pending mastery question.

    Shared by ``mastery_quiz``'s returned payload and the loop's ask_user
    binding, so the confidence tab survives pause/resume turns and the tutor
    always sees both questions on the card.
    """
    return [
        public_pending_question(pending).to_ask_user_dict(),
        confidence_ask_user_question(pending.question_id),
    ]


__all__ = [
    "OPTION_PREFIX_RE",
    "CONFIDENCE_OPTIONS",
    "CONFIDENCE_QUESTION_ID_SUFFIX",
    "CONFIDENCE_QUESTION_PROMPT",
    "PublicPendingOption",
    "PublicPendingQuestion",
    "confidence_ask_user_question",
    "format_options",
    "has_option_bodies",
    "parse_options",
    "pending_ask_user_questions",
    "public_pending_question",
    "resolve_answer",
    "resolve_choice_submission",
]
