"""Unit tests for the learning evidence store and its fail-open service hooks."""

from pathlib import Path
import sqlite3

import pytest

from deeptutor.learning.evidence_store import EvidenceStore
from deeptutor.learning.models import (
    KnowledgePoint,
    KnowledgeType,
    LearningEvidence,
    LearningModule,
    LearningProgress,
)
from deeptutor.learning.service import LearningService
from deeptutor.learning.storage import LearningStore


@pytest.fixture
def evidence_store(tmp_path: Path) -> EvidenceStore:
    return EvidenceStore(db_path=tmp_path / "learning_evidence.db")


@pytest.fixture
def service(tmp_path: Path, evidence_store: EvidenceStore) -> LearningService:
    return LearningService(
        store=LearningStore(root=tmp_path / "learning"),
        evidence_store=evidence_store,
    )


def _make_progress(book_id: str = "book1") -> LearningProgress:
    return LearningProgress(
        book_id=book_id,
        modules=[
            LearningModule(
                id="m1",
                name="M1",
                order=0,
                knowledge_points=[
                    KnowledgePoint(
                        id="kp1",
                        name="KP1",
                        type=KnowledgeType.MEMORY,
                        module_id="m1",
                    )
                ],
            )
        ],
    )


# ── append / query roundtrip ─────────────────────────────────────────────


class TestAppendQuery:
    def test_append_and_query_roundtrip(self, evidence_store):
        evidence = LearningEvidence(
            user_id="u1",
            book_id="b1",
            kp_id="kp1",
            question_id="q1",
            session_id="s1",
            evidence_type="graded_quiz",
            is_correct=False,
            cognitive_gate="retrieval",
            error_type="application",
            confidence_before=40,
            confidence_after=30,
            hint_level_reached=2,
            detail_json={"expected": "42", "actual": "wrong"},
        )
        row_id = evidence_store.append(evidence)
        assert isinstance(row_id, int) and row_id > 0

        rows = evidence_store.query_evidence()
        assert len(rows) == 1
        ev = rows[0]
        assert ev.id == row_id
        assert ev.user_id == "u1"
        assert ev.book_id == "b1"
        assert ev.kp_id == "kp1"
        assert ev.question_id == "q1"
        assert ev.session_id == "s1"
        assert ev.evidence_type == "graded_quiz"
        assert ev.is_correct is False
        assert ev.passed is None
        assert ev.cognitive_gate == "retrieval"
        assert ev.error_type == "application"
        assert ev.confidence_before == 40
        assert ev.confidence_after == 30
        assert ev.hint_level_reached == 2
        assert ev.detail_json == {"expected": "42", "actual": "wrong"}

    def test_append_is_append_only(self, evidence_store):
        first = evidence_store.append(
            LearningEvidence(evidence_type="graded_quiz", is_correct=True)
        )
        second = evidence_store.append(
            LearningEvidence(evidence_type="qualitative_gate", passed=True)
        )
        rows = evidence_store.query_evidence()
        assert sorted(r.id for r in rows) == [first, second]

    def test_rows_queryable_via_raw_sqlite(self, evidence_store, tmp_path):
        evidence_store.append(
            LearningEvidence(
                user_id="u1",
                book_id="b1",
                kp_id="kp1",
                evidence_type="graded_quiz",
                is_correct=True,
            )
        )
        conn = sqlite3.connect(tmp_path / "learning_evidence.db")
        try:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            row = conn.execute(
                "SELECT evidence_type, is_correct FROM learning_evidence"
            ).fetchone()
        finally:
            conn.close()
        assert row == ("graded_quiz", 1)

    def test_query_orders_newest_first(self, evidence_store):
        first = evidence_store.append(
            LearningEvidence(evidence_type="graded_quiz", is_correct=True, created_at=100.0)
        )
        second = evidence_store.append(
            LearningEvidence(evidence_type="graded_quiz", is_correct=False, created_at=200.0)
        )
        rows = evidence_store.query_evidence()
        assert [r.id for r in rows] == [second, first]

    def test_query_limit(self, evidence_store):
        for i in range(3):
            evidence_store.append(
                LearningEvidence(
                    evidence_type="graded_quiz", is_correct=True, created_at=float(i)
                )
            )
        rows = evidence_store.query_evidence(limit=2)
        assert len(rows) == 2


# ── filters ──────────────────────────────────────────────────────────────


class TestQueryFilters:
    def test_filters_by_kp(self, evidence_store):
        evidence_store.append(
            LearningEvidence(evidence_type="graded_quiz", is_correct=True, kp_id="kp1")
        )
        evidence_store.append(
            LearningEvidence(evidence_type="graded_quiz", is_correct=True, kp_id="kp2")
        )
        rows = evidence_store.query_evidence(kp_id="kp1")
        assert len(rows) == 1
        assert rows[0].kp_id == "kp1"

    def test_filters_by_time_window(self, evidence_store):
        evidence_store.append(
            LearningEvidence(evidence_type="graded_quiz", is_correct=True, created_at=100.0)
        )
        evidence_store.append(
            LearningEvidence(evidence_type="graded_quiz", is_correct=True, created_at=200.0)
        )
        rows = evidence_store.query_evidence(since=150.0)
        assert len(rows) == 1
        assert rows[0].created_at == 200.0

    def test_filters_by_book_and_user(self, evidence_store):
        evidence_store.append(
            LearningEvidence(
                evidence_type="graded_quiz", is_correct=True, user_id="u1", book_id="b1"
            )
        )
        evidence_store.append(
            LearningEvidence(
                evidence_type="graded_quiz", is_correct=True, user_id="u1", book_id="b2"
            )
        )
        evidence_store.append(
            LearningEvidence(
                evidence_type="graded_quiz", is_correct=True, user_id="u2", book_id="b1"
            )
        )
        rows = evidence_store.query_evidence(user_id="u1", book_id="b1")
        assert len(rows) == 1


# ── count_for_kp ─────────────────────────────────────────────────────────


class TestCountForKp:
    def test_counts_per_user_and_kp(self, evidence_store):
        for _ in range(3):
            evidence_store.append(
                LearningEvidence(
                    evidence_type="graded_quiz", is_correct=True, user_id="u1", kp_id="kp1"
                )
            )
        evidence_store.append(
            LearningEvidence(
                evidence_type="qualitative_gate", passed=True, user_id="u1", kp_id="kp1"
            )
        )
        evidence_store.append(
            LearningEvidence(
                evidence_type="graded_quiz", is_correct=True, user_id="u2", kp_id="kp1"
            )
        )
        assert evidence_store.count_for_kp("u1", "kp1") == 4
        assert evidence_store.count_for_kp("u2", "kp1") == 1
        assert evidence_store.count_for_kp("u3", "kp1") == 0


# ── service hooks (fail-open) ────────────────────────────────────────────


class TestServiceHooks:
    def test_grade_and_record_writes_graded_quiz_evidence(self, service, evidence_store):
        progress = _make_progress()
        result = service.grade_and_record(
            progress,
            question_id="q1",
            knowledge_point_id="kp1",
            module_id="m1",
            user_answer="42",
            expected_answer="42",
            question_type="short",
            user_id="u1",
            session_id="s1",
        )
        assert result is True
        rows = evidence_store.query_evidence(user_id="u1")
        assert len(rows) == 1
        ev = rows[0]
        assert ev.evidence_type == "graded_quiz"
        assert ev.is_correct is True
        assert ev.passed is None
        assert ev.cognitive_gate == "retrieval"
        assert ev.error_type == ""
        assert ev.kp_id == "kp1"
        assert ev.book_id == "book1"
        assert ev.question_id == "q1"
        assert ev.session_id == "s1"

    def test_grade_and_record_classifies_wrong_answer(self, service, evidence_store):
        progress = _make_progress()
        result = service.grade_and_record(
            progress,
            question_id="q2",
            knowledge_point_id="kp1",
            module_id="m1",
            user_answer="wrong",
            expected_answer="42",
            user_id="u1",
        )
        assert result is False
        ev = evidence_store.query_evidence()[0]
        assert ev.is_correct is False
        assert ev.error_type == "application"

    def test_record_qualitative_writes_gate_evidence(self, service, evidence_store):
        progress = _make_progress()
        service.record_qualitative(
            progress,
            "kp1",
            passed=True,
            evidence="clear explanation",
            user_id="u1",
            session_id="s1",
        )
        rows = evidence_store.query_evidence(user_id="u1")
        assert len(rows) == 1
        ev = rows[0]
        assert ev.evidence_type == "qualitative_gate"
        assert ev.passed is True
        assert ev.is_correct is None
        assert ev.cognitive_gate == "self_explanation"
        assert ev.kp_id == "kp1"
        assert ev.book_id == "book1"
        assert ev.detail_json == {"evidence": "clear explanation"}

    def test_evidence_append_failure_is_fail_open(self, service, evidence_store, monkeypatch):
        def boom(_evidence):
            raise RuntimeError("evidence db is down")

        monkeypatch.setattr(evidence_store, "append", boom)
        progress = _make_progress()
        result = service.grade_and_record(
            progress,
            question_id="q1",
            knowledge_point_id="kp1",
            module_id="m1",
            user_answer="42",
            expected_answer="42",
        )
        assert result is True
        # The main pipeline still recorded the attempt and persisted.
        assert len(progress.quiz_attempts) == 1
        assert progress.quiz_attempts[0].is_correct is True

    def test_evidence_store_construction_failure_is_fail_open(self, tmp_path, monkeypatch):
        service = LearningService(store=LearningStore(root=tmp_path / "learning"))

        def boom():
            raise RuntimeError("cannot open evidence db")

        monkeypatch.setattr("deeptutor.learning.service.EvidenceStore", boom)
        progress = _make_progress()
        result = service.grade_and_record(
            progress,
            question_id="q1",
            knowledge_point_id="kp1",
            module_id="m1",
            user_answer="42",
            expected_answer="42",
        )
        assert result is True
        assert len(progress.quiz_attempts) == 1
