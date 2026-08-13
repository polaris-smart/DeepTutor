"""Stable artifact for questions parsed from an exam paper."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256


@dataclass(frozen=True)
class ParsedQuestion:
    """A source question whose identity is independent of paper ordering.

    Only the normalized question text contributes to ``stable_id``. Source
    location and extractor classifications are metadata and may change when a
    paper is reordered or parsed again.
    """

    question_text: str
    question_type: str = "written"
    difficulty: str = "medium"
    answer: str | None = None
    options: dict[str, str] | None = None
    source_paper: str = ""
    original_index: int = 0
    stable_id: str = field(init=False)

    def __post_init__(self) -> None:
        normalized_text = " ".join(self.question_text.split())
        digest = sha256(normalized_text.encode("utf-8")).hexdigest()
        object.__setattr__(self, "stable_id", digest[:16])


__all__ = ["ParsedQuestion"]
