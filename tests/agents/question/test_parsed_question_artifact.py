from __future__ import annotations

import json
from pathlib import Path

import pytest

from deeptutor.agents.question.artifact_guard import (
    ArtifactConservationError,
    validate_conservation,
)
from deeptutor.agents.question.mimic_source import _parse_sync
from deeptutor.agents.question.parsed_question import ParsedQuestion
from deeptutor.agents.question.pipeline import QuizTemplate


def _parsed(question_text: str, original_index: int) -> ParsedQuestion:
    return ParsedQuestion(
        question_text=question_text,
        question_type="written",
        difficulty="medium",
        source_paper="exam.pdf",
        original_index=original_index,
    )


def _derived(question_id: str) -> QuizTemplate:
    return QuizTemplate(
        question_id=question_id,
        topic="topic",
        question_type="written",
        difficulty="medium",
    )


def test_same_question_keeps_stable_id_after_reordering() -> None:
    before = _parsed("Explain the Pythagorean theorem.", original_index=1)
    after = _parsed("Explain the Pythagorean theorem.", original_index=7)

    assert before.stable_id == after.stable_id


def test_different_questions_do_not_collide_when_order_is_swapped() -> None:
    first_order = [
        _parsed("What is 2 + 2?", original_index=1),
        _parsed("What is 3 + 3?", original_index=2),
    ]
    swapped_order = [
        _parsed("What is 3 + 3?", original_index=1),
        _parsed("What is 2 + 2?", original_index=2),
    ]

    ids_by_text = {question.question_text: question.stable_id for question in first_order}
    swapped_ids_by_text = {question.question_text: question.stable_id for question in swapped_order}

    assert len(set(ids_by_text.values())) == 2
    assert ids_by_text == swapped_ids_by_text


def test_conservation_rejects_extra_derived_questions() -> None:
    original = [_parsed("Original", original_index=1)]
    derived = [_derived(original[0].stable_id), _derived("extra")]

    with pytest.raises(ArtifactConservationError, match="count"):
        validate_conservation(original, derived)


def test_conservation_rejects_duplicate_ids() -> None:
    original = [
        _parsed("First", original_index=1),
        _parsed("Second", original_index=2),
    ]
    derived = [_derived(original[0].stable_id), _derived(original[0].stable_id)]

    with pytest.raises(ArtifactConservationError, match="duplicate"):
        validate_conservation(original, derived)


def test_conservation_rejects_unknown_ids() -> None:
    original = [_parsed("Original", original_index=1)]

    with pytest.raises(ArtifactConservationError, match="unknown"):
        validate_conservation(original, [_derived("not-from-the-paper")])


def test_conservation_accepts_valid_subset() -> None:
    original = [
        _parsed("First", original_index=1),
        _parsed("Second", original_index=2),
    ]

    assert validate_conservation(original, [_derived(original[1].stable_id)]) is None


def test_mimic_mapping_uses_parsed_question_stable_id(tmp_path: Path) -> None:
    paper = tmp_path / "exam"
    paper.mkdir()
    question_text = "Explain conservation of momentum."
    (paper / "exam_questions.json").write_text(
        json.dumps({"questions": [{"question_text": question_text}]}),
        encoding="utf-8",
    )

    templates, _ = _parse_sync(paper, 10, "parsed", tmp_path / "out")

    assert templates[0].question_id == _parsed(question_text, original_index=99).stable_id
