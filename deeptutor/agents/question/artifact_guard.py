"""Conservation checks for derived question artifacts."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deeptutor.agents.question.parsed_question import ParsedQuestion
    from deeptutor.agents.question.pipeline import QuizTemplate


class ArtifactConservationError(ValueError):
    """Raised when derived questions cannot be traced to source questions."""


def validate_conservation(
    original: list[ParsedQuestion],
    derived: list[QuizTemplate],
) -> None:
    """Ensure derived templates neither multiply nor invent source questions."""
    if len(derived) > len(original):
        raise ArtifactConservationError(
            f"Derived question count {len(derived)} exceeds original count {len(original)}"
        )

    derived_ids = [question.question_id for question in derived]
    if len(derived_ids) != len(set(derived_ids)):
        raise ArtifactConservationError("Derived questions contain duplicate question_id values")

    original_ids = {question.stable_id for question in original}
    unknown_ids = set(derived_ids) - original_ids
    if unknown_ids:
        rendered = ", ".join(sorted(unknown_ids))
        raise ArtifactConservationError(
            f"Derived questions contain unknown question_id values: {rendered}"
        )


__all__ = ["ArtifactConservationError", "validate_conservation"]
