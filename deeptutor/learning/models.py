from __future__ import annotations

from enum import Enum
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_KNOWLEDGE_TYPE_LEGACY: dict[str, str] = {
    "记忆型": "memory",
    "概念型": "concept",
    "程序型": "procedure",
    "设计型": "design",
}

_ERROR_TYPE_LEGACY: dict[str, str] = {
    "知识结构性": "structural",
    "理解偏差型": "deviation",
    "应用错误": "application",
    "元认知型": "metacognitive",
}


class KnowledgeType(str, Enum):
    MEMORY = "memory"
    CONCEPT = "concept"
    PROCEDURE = "procedure"
    DESIGN = "design"

    @classmethod
    def _missing_(cls, value: object) -> KnowledgeType | None:
        mapped = _KNOWLEDGE_TYPE_LEGACY.get(str(value))
        return cls(mapped) if mapped else None


class ErrorType(str, Enum):
    KNOWLEDGE_STRUCTURAL = "structural"
    UNDERSTANDING_DEVIATION = "deviation"
    APPLICATION_ERROR = "application"
    METACOGNITIVE = "metacognitive"

    @classmethod
    def _missing_(cls, value: object) -> ErrorType | None:
        mapped = _ERROR_TYPE_LEGACY.get(str(value))
        return cls(mapped) if mapped else None


SixDimensionKey = Literal[
    "knowledge",
    "procedure",
    "understanding",
    "transfer",
    "retention",
    "habit",
]
SixDimensionDataState = Literal["scored", "insufficient"]
SixDimensionEvidenceKind = Literal["attempt", "error", "review", "route_task"]


class SixDimensionEvidenceRef(BaseModel):
    """Stable pointer to one item of evidence behind a dimension score."""

    kind: SixDimensionEvidenceKind
    id: str = Field(min_length=1)


class SixDimensionResult(BaseModel):
    """One explainable dimension in a :class:`SixDimensionSnapshot`."""

    key: SixDimensionKey
    score: float | None = Field(default=None, ge=0, le=100)
    data_state: SixDimensionDataState
    confidence: float = Field(ge=0, le=1)
    evidence_count: int = Field(ge=0)
    evidence_refs: list[SixDimensionEvidenceRef] = Field(default_factory=list)
    explanation: str
    next_action: str

    @model_validator(mode="after")
    def _validate_score_and_evidence(self) -> SixDimensionResult:
        if self.evidence_count != len(self.evidence_refs):
            raise ValueError("evidence_count must match evidence_refs")
        if self.data_state == "scored":
            if self.score is None:
                raise ValueError("scored dimensions require a score")
            if not self.evidence_refs:
                raise ValueError("scored dimensions require traceable evidence")
        elif self.score is not None:
            raise ValueError("insufficient dimensions must use a null score")
        return self


class SixDimensionSnapshot(BaseModel):
    """A point-in-time, evidence-backed learner profile for one book."""

    book_id: str
    generated_at: float = Field(default_factory=time.time)
    dimensions: list[SixDimensionResult]
    overall: float | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def _validate_complete_dimension_set(self) -> SixDimensionSnapshot:
        expected = {
            "knowledge",
            "procedure",
            "understanding",
            "transfer",
            "retention",
            "habit",
        }
        keys = [dimension.key for dimension in self.dimensions]
        if len(keys) != 6 or set(keys) != expected:
            raise ValueError("dimensions must contain each six-dimension key exactly once")
        return self


# Stages removed in the Mastery Path simplification are mapped onto the nearest
# surviving stage so progress persisted by the older engine still deserializes.
_STAGE_LEGACY: dict[str, str] = {
    "diagnostic_phase1": "diagnostic",
    "diagnostic_phase2": "diagnostic",
    "metacognitive_intro": "explain",
    "plan": "explain",
    "pretest": "explain",
    "practice_quiz": "practice",
    "module_test": "review",
}


class LearningStage(str, Enum):
    """The Mastery Path loop: diagnose once, then per knowledge point teach and
    check understanding, then practice the module, diagnose errors, and schedule
    spaced review."""

    DIAGNOSTIC = "diagnostic"
    EXPLAIN = "explain"
    FEYNMAN_CHECK = "feynman_check"
    PRACTICE = "practice"
    ERROR_DIAGNOSIS = "error_diagnosis"
    REVIEW = "review"
    COMPLETED = "completed"

    @classmethod
    def _missing_(cls, value: object) -> LearningStage | None:
        mapped = _STAGE_LEGACY.get(str(value))
        return cls(mapped) if mapped else None


class KnowledgePoint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    type: KnowledgeType
    module_id: str
    # Stable bridge to the doc-intel textbook-tree node this KP was derived
    # from: the "/"-joined heading chain (``struct_path``) and the node's
    # stable id. Both default to "" so paths built before the bridge existed
    # (and KPs the model authored without a textbook anchor) keep working —
    # matching falls back to names exactly as before.
    struct_path: str = ""
    textbook_node_id: str = ""


class LearningModule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    order: int
    pass_threshold: float = 0.7
    knowledge_points: list[KnowledgePoint] = Field(default_factory=list)


class DiagnosticResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    total_questions: int = 0
    correct_count: int = 0
    module_mastery: dict[str, float] = Field(default_factory=dict)


class QuizAttempt(BaseModel):
    model_config = ConfigDict(extra="ignore")

    question_id: str
    knowledge_point_id: str
    module_id: str = ""
    is_correct: bool
    user_answer: Any = None
    error_type: ErrorType | None = None
    self_attribution: str = ""
    mastery_estimate: float = 0.0
    timestamp: float = Field(default_factory=time.time)


class RetryAttempt(BaseModel):
    model_config = ConfigDict(extra="ignore")

    timestamp: float
    is_correct: bool
    attempt_number: int


class ErrorRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    question_id: str
    knowledge_point_id: str
    module_id: str
    error_type: ErrorType
    self_attribution: str = ""
    ai_confirmation: str = ""
    retry_history: list[RetryAttempt] = Field(default_factory=list)
    status: Literal["active", "retrying", "review", "graduated"] = "active"
    created_at: float = Field(default_factory=time.time)


class RepetitionState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    interval_index: int = 0
    consecutive_correct: int = 0
    consecutive_wrong: int = 0
    next_review_at: float


class ReviewTask(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    knowledge_point_id: str
    knowledge_type: KnowledgeType
    due_at: float
    priority: int
    state: RepetitionState


class PendingQuestion(BaseModel):
    """A question posed to the learner and awaiting their answer.

    Persisted so grading is deterministic across turns: the expected answer
    lives here server-side and never round-trips through the model. The tutor
    poses a question with ``mastery_quiz`` (storing this), the learner answers
    on a later turn, and ``mastery_grade`` scores the stored answer.
    """

    model_config = ConfigDict(extra="ignore")

    question_id: str
    knowledge_point_id: str
    module_id: str = ""
    prompt: str = ""
    question_type: str = "short"
    expected_answer: str = ""
    options: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)


class LearningProgress(BaseModel):
    model_config = ConfigDict(extra="ignore")

    book_id: str
    diagnostic: DiagnosticResult | None = None
    modules: list[LearningModule] = Field(default_factory=list)
    current_module_id: str = ""
    current_stage: LearningStage = LearningStage.DIAGNOSTIC
    current_kp_index: int = 0
    mastery_levels: dict[str, float] = Field(default_factory=dict)
    # Qualitative gate for CONCEPT / DESIGN knowledge points: True once the
    # tutor judges the learner's explanation sufficient (``mastery_assess``).
    # The quantitative ``mastery_levels`` gate covers MEMORY / PROCEDURE.
    qualitative_mastery: dict[str, bool] = Field(default_factory=dict)
    knowledge_types: dict[str, KnowledgeType] = Field(default_factory=dict)
    quiz_attempts: list[QuizAttempt] = Field(default_factory=list)
    error_records: list[ErrorRecord] = Field(default_factory=list)
    repetition_states: dict[str, RepetitionState] = Field(default_factory=dict)
    review_queue: list[ReviewTask] = Field(default_factory=list)
    # A single outstanding question; grading reads its expected answer so the
    # model never has to recall it across turns.
    pending_question: PendingQuestion | None = None
    feynman_retries: dict[str, int] = Field(default_factory=dict)
    feynman_explanations: dict[str, str] = Field(default_factory=dict)
    stage_failure_counts: dict[str, int] = Field(default_factory=dict)
    stage_failure_notes: dict[str, str] = Field(default_factory=dict)
    version: int = 0
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class LearningEvidence(BaseModel):
    """One immutable row of learner evidence (collection-side payload).

    Written by the fail-open hooks in :class:`LearningService` and persisted
    append-only by ``deeptutor.learning.evidence_store.EvidenceStore``. Field
    names align with the ``evidence_captured`` schema of education-agent-skills
    so the engine's decision side can query a stable, cross-book evidence
    stream. ``confidence_*`` / ``hint_level_reached`` are reserved for the
    tutor-persona capture pass and stay NULL until then.
    """

    model_config = ConfigDict(extra="ignore")

    #: Row id assigned by SQLite on append; None until persisted.
    id: int | None = None
    created_at: float = Field(default_factory=time.time)
    user_id: str = ""
    book_id: str = ""
    kp_id: str = ""
    question_id: str = ""
    session_id: str = ""
    evidence_type: Literal["graded_quiz", "qualitative_gate"]
    is_correct: bool | None = None
    passed: bool | None = None
    cognitive_gate: str = ""
    confidence_before: int | None = None
    confidence_after: int | None = None
    hint_level_reached: int | None = None
    error_type: str = ""
    detail_json: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "KnowledgeType",
    "ErrorType",
    "SixDimensionKey",
    "SixDimensionDataState",
    "SixDimensionEvidenceKind",
    "SixDimensionEvidenceRef",
    "SixDimensionResult",
    "SixDimensionSnapshot",
    "LearningStage",
    "KnowledgePoint",
    "LearningModule",
    "DiagnosticResult",
    "QuizAttempt",
    "RetryAttempt",
    "ErrorRecord",
    "RepetitionState",
    "ReviewTask",
    "PendingQuestion",
    "LearningProgress",
    "LearningEvidence",
]
