"""Mastery Path — structured mastery-based learning engine.

Modules:
    models      — Pydantic data models
    storage     — JSON persistence
    scheduler   — Spaced repetition
    mastery     — Mastery scoring policy (swappable)
    grading     — Deterministic answer grading
    service     — Business logic
    prompts     — LLM prompt templates
"""

from deeptutor.learning.models import (
    DiagnosticResult,
    ErrorRecord,
    ErrorType,
    KnowledgePoint,
    KnowledgeType,
    LearningEvidence,
    LearningModule,
    LearningProgress,
    LearningStage,
    QuizAttempt,
    ReadingActivityRecord,
    ReadingLearningRecords,
    ReadingProgressRecord,
    RepetitionState,
    RetryAttempt,
    ReviewTask,
    SixDimensionDataState,
    SixDimensionEvidenceKind,
    SixDimensionEvidenceRef,
    SixDimensionKey,
    SixDimensionResult,
    SixDimensionSnapshot,
)

__all__ = [
    "DiagnosticResult",
    "ErrorRecord",
    "ErrorType",
    "KnowledgePoint",
    "KnowledgeType",
    "LearningEvidence",
    "LearningModule",
    "LearningProgress",
    "LearningStage",
    "MasteryEvent",
    "MasteryInteraction",
    "MasteryPathLease",
    "PendingQuestion",
    "ReadingActivityRecord",
    "ReadingLearningRecords",
    "ReadingProgressRecord",
    "QuizAttempt",
    "RepetitionState",
    "RetryAttempt",
    "ReviewTask",
    "SixDimensionDataState",
    "SixDimensionEvidenceKind",
    "SixDimensionEvidenceRef",
    "SixDimensionKey",
    "SixDimensionResult",
    "SixDimensionSnapshot",
]
