"""Question tools with dependency-light, backward-compatible public exports."""

from __future__ import annotations

import importlib

_LAZY_EXPORTS = {
    "MinerUConfig": ("deeptutor.services.parsing.engines.mineru.config", "MinerUConfig"),
    "MinerUError": ("deeptutor.services.parsing.engines.mineru.config", "MinerUError"),
    "extract_questions_from_paper": (
        "deeptutor.tools.question.question_extractor",
        "extract_questions_from_paper",
    ),
    "parse_pdf_to_workdir": (
        "deeptutor.services.parsing.engines.mineru.backend",
        "parse_pdf_to_workdir",
    ),
    "parse_pdf_with_mineru": (
        "deeptutor.services.parsing.engines.mineru.local",
        "parse_pdf_with_mineru",
    ),
    "resolve_mineru_config": (
        "deeptutor.services.parsing.engines.mineru.config",
        "resolve_mineru_config",
    ),
}


def __getattr__(name: str):
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = _LAZY_EXPORTS[name]
    value = getattr(importlib.import_module(module_name), attribute_name)
    globals()[name] = value
    return value


async def mimic_exam_questions(*args, **kwargs):
    """
    Lazy wrapper to avoid circular imports with question coordinator.
    """
    from .exam_mimic import mimic_exam_questions as _mimic_exam_questions

    return await _mimic_exam_questions(*args, **kwargs)


__all__ = [
    "MinerUConfig",
    "MinerUError",
    "parse_pdf_to_workdir",
    "parse_pdf_with_mineru",
    "resolve_mineru_config",
    "extract_questions_from_paper",
    "mimic_exam_questions",
]
