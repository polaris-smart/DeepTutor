from __future__ import annotations

import json
from pathlib import Path

import pytest

from deeptutor.agents.question.parsed_question import ParsedQuestion
from deeptutor.tools.question.paper_artifact import (
    load_paper_artifact,
    paper_artifact_from_extractor_json,
)
from deeptutor.tools.question.paper_reorder import (
    PaperExportError,
    PaperReorderError,
    PaperReorderRequest,
    ensure_exportable,
    render_paper_export,
    reorder_paper,
)


def _artifact():
    return paper_artifact_from_extractor_json(
        {
            "paper_name": "期中卷",
            "questions": [
                {
                    "question_number": "一、1",
                    "question_text": "Easy choice",
                    "question_type": "choice",
                    "difficulty": "easy",
                    "answer": "A",
                    "knowledge_point_ids": ["kp-a"],
                    "images": [],
                },
                {
                    "question_number": "2",
                    "question_text": "Hard written",
                    "question_type": "written",
                    "difficulty": "hard",
                    "answer": "Proof",
                    "knowledge_point_ids": ["kp-b"],
                    "images": [],
                },
                {
                    "question_number": "3",
                    "question_text": "Medium choice",
                    "question_type": "choice",
                    "difficulty": "medium",
                    "answer": "C",
                    "knowledge_point_ids": ["kp-a"],
                    "images": [],
                },
            ],
        },
        source_id="paper/questions.json",
    )


def _request(*, sections: list[dict], policy: str = "append", answers: bool = False):
    return PaperReorderRequest.model_validate(
        {
            "source_id": "paper/questions.json",
            "rules": {"sections": sections, "unassigned_policy": policy},
            "include_answer_sheet": answers,
        }
    )


def test_reorder_conserves_every_source_question_and_rewrites_display_numbers() -> None:
    artifact = _artifact()
    assert all(isinstance(item.parsed_question, ParsedQuestion) for item in artifact.questions)

    paper = reorder_paper(
        artifact,
        _request(
            sections=[
                {
                    "id": "choice",
                    "title": "选择题",
                    "filter": {"question_type": "choice"},
                    "order": "source",
                },
                {
                    "id": "written",
                    "title": "解答题",
                    "filter": {"question_type": "written"},
                    "order": "source",
                },
            ]
        ),
    )

    output = [question for section in paper.sections for question in section.questions]
    assert paper.audit.input_count == paper.audit.output_count == 3
    assert paper.audit.unassigned_ids == []
    assert paper.audit.duplicate_ids == []
    assert [question.display_number for question in output] == ["1", "2", "3"]
    assert {question.source_question_id for question in output} == {
        question.stable_id for question in artifact.questions
    }
    assert paper.answer_sheet is None
    ensure_exportable(paper)


def test_easy_to_hard_sort_is_stable_with_source_order_as_tie_breaker() -> None:
    artifact = paper_artifact_from_extractor_json(
        {
            "questions": [
                {"question_text": "Hard", "difficulty": "hard"},
                {"question_text": "Easy first", "difficulty": "easy"},
                {"question_text": "Easy second", "difficulty": "easy"},
                {"question_text": "Medium", "difficulty": "medium"},
            ]
        },
        source_id="paper/questions.json",
    )
    paper = reorder_paper(
        artifact,
        _request(
            sections=[
                {
                    "id": "all",
                    "title": "由易到难",
                    "order": "easy_to_hard",
                }
            ]
        ),
    )

    assert [question.question_text for question in paper.sections[0].questions] == [
        "Easy first",
        "Easy second",
        "Medium",
        "Hard",
    ]


def test_reject_policy_reports_every_unassigned_question() -> None:
    artifact = _artifact()

    with pytest.raises(PaperReorderError, match="unassigned") as caught:
        reorder_paper(
            artifact,
            _request(
                sections=[
                    {
                        "id": "written",
                        "title": "解答题",
                        "filter": {"question_type": "written"},
                        "order": "source",
                    }
                ],
                policy="reject",
            ),
        )

    assert caught.value.audit is not None
    assert caught.value.audit.output_count == 1
    assert caught.value.audit.unassigned_ids == [
        artifact.questions[0].stable_id,
        artifact.questions[2].stable_id,
    ]


def test_duplicate_manual_ids_are_audited_deduplicated_and_block_export() -> None:
    artifact = _artifact()
    first_id = artifact.questions[0].stable_id
    paper = reorder_paper(
        artifact,
        _request(
            sections=[
                {
                    "id": "manual",
                    "title": "手动",
                    "order": "manual",
                    "question_ids": [first_id, first_id],
                }
            ]
        ),
    )

    assert paper.audit.input_count == paper.audit.output_count == 3
    assert paper.audit.duplicate_ids == [first_id]
    with pytest.raises(PaperExportError, match="duplicate"):
        render_paper_export(paper, "markdown")


def test_missing_image_reference_blocks_html_and_markdown_export(tmp_path: Path) -> None:
    question_file = tmp_path / "exam_questions.json"
    question_file.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_text": "Read the missing diagram",
                        "images": ["images/diagram.png"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    artifact = load_paper_artifact(
        question_file,
        source_id="paper/questions.json",
        allowed_image_root=tmp_path,
    )
    paper = reorder_paper(
        artifact,
        _request(sections=[{"id": "all", "title": "全部", "order": "source"}]),
    )

    assert artifact.missing_image_refs
    for export_format in ("html", "markdown"):
        with pytest.raises(PaperExportError, match="image"):
            render_paper_export(paper, export_format)


def test_student_export_never_contains_answers_but_teacher_sheet_is_traceable() -> None:
    artifact = _artifact()
    section = [{"id": "all", "title": "全部", "order": "source"}]
    student = reorder_paper(artifact, _request(sections=section))
    teacher = reorder_paper(artifact, _request(sections=section, answers=True))

    assert "Proof" not in render_paper_export(student, "markdown")
    assert teacher.answer_sheet is not None
    assert len({entry.source_question_id for entry in teacher.answer_sheet}) == 3
    assert "Proof" in render_paper_export(teacher, "markdown")
