from __future__ import annotations

import asyncio
from io import BytesIO

from fastapi import UploadFile
import pytest
from starlette.datastructures import Headers

from deeptutor.api.routers import book as book_router
from deeptutor.book.models import (
    Block,
    BlockStatus,
    BlockType,
    Book,
    Page,
    RecitationSummary,
)
from deeptutor.book.recitation import (
    resolve_target_lines,
    score_recitation,
    stt_failed_attempt,
    update_recitation_summary,
)


def _score(expected: str, transcript: str):
    return score_recitation(
        book_id="book-1",
        block_id="poem-1",
        target_lines=[("line-1", expected)],
        transcript=transcript,
    )


def test_character_alignment_ignores_punctuation_and_aggregates_lines() -> None:
    attempt = score_recitation(
        book_id="book-1",
        block_id="poem-1",
        target_lines=[
            ("line-1", "床前明月光，"),
            ("line-2", "疑是地上霜。"),
        ],
        transcript="床前明月光。疑是地上霜",
    )

    assert attempt.data_state == "scored"
    assert attempt.overall_accuracy == 1.0
    assert [result.recognized_text for result in attempt.line_results] == [
        "床前明月光",
        "疑是地上霜",
    ]
    assert all(result.character_accuracy == 1.0 for result in attempt.line_results)


def test_character_alignment_reports_omission() -> None:
    attempt = _score("床前明月光", "床前明月")

    result = attempt.line_results[0]
    assert result.recognized_text == "床前明月"
    assert result.omissions == ["光"]
    assert result.insertions == []
    assert result.character_accuracy == pytest.approx(0.8)
    assert attempt.overall_accuracy == pytest.approx(0.8)


def test_character_alignment_reports_insertion() -> None:
    attempt = _score("床前明月光", "床前的明月光")

    result = attempt.line_results[0]
    assert result.recognized_text == "床前的明月光"
    assert result.omissions == []
    assert result.insertions == ["的"]
    assert result.character_accuracy == pytest.approx(0.8333)
    assert attempt.overall_accuracy == pytest.approx(0.8333)


def test_target_text_is_resolved_from_poetry_payload() -> None:
    target_lines = resolve_target_lines(
        [
            {"text": "床前明月光", "pinyin": "chuang qian"},
            {"text": "疑是地上霜", "pinyin": "yi shi"},
        ],
        ["line-2"],
    )

    assert target_lines == [("line-2", "疑是地上霜")]


def test_stt_failure_has_no_pseudo_zero_and_preserves_best_score() -> None:
    attempt = stt_failed_attempt(
        book_id="book-1",
        block_id="poem-1",
        target_line_ids=["line-1"],
    )
    summary = update_recitation_summary(
        RecitationSummary(attempt_count=2, latest_accuracy=0.9, best_accuracy=0.9),
        attempt,
    )

    assert attempt.data_state == "stt_failed"
    assert attempt.overall_accuracy is None
    assert attempt.line_results == []
    assert summary.attempt_count == 3
    assert summary.latest_accuracy is None
    assert summary.best_accuracy == 0.9


def test_recitation_endpoint_returns_unscored_state_when_stt_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    block = Block(
        id="poem-1",
        type=BlockType.POETRY,
        status=BlockStatus.READY,
        payload={"lines": [{"text": "床前明月光"}]},
    )
    page = Page(id="page-1", book_id="book-1", blocks=[block])

    class _Storage:
        saved_pages: list[Page] = []

        def save_page(self, saved_page: Page) -> None:
            self.saved_pages.append(saved_page)

    class _Engine:
        storage = _Storage()

        def load_book(self, book_id: str) -> Book | None:
            return Book(id=book_id, language="zh") if book_id == "book-1" else None

        def list_pages(self, book_id: str) -> list[Page]:
            return [page] if book_id == "book-1" else []

    async def _failed_stt(*args: object, **kwargs: object) -> str:
        raise RuntimeError("provider unavailable")

    engine = _Engine()
    monkeypatch.setattr(book_router, "get_book_engine", lambda: engine)
    monkeypatch.setattr(book_router, "transcribe_audio", _failed_stt)
    upload = UploadFile(
        file=BytesIO(b"audio"),
        filename="recitation.webm",
        headers=Headers({"content-type": "audio/webm"}),
    )

    response = asyncio.run(
        book_router.submit_recitation(
            book_id="book-1",
            block_id="poem-1",
            line_ids=["line-1"],
            audio=upload,
        )
    )

    assert response["attempt"]["data_state"] == "stt_failed"
    assert response["attempt"]["overall_accuracy"] is None
    assert response["summary"]["latest_accuracy"] is None
    assert engine.storage.saved_pages == [page]
    assert "transcript" not in block.metadata["recitation"]
