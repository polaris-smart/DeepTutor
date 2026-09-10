"""T034: 数学 8 类接入 page_planner。

覆盖任务书验收点：
1. ``_PHASE1_TYPES`` 白名单包含数学 8 类（DESMOS/GEOGEBRA/GEOMETRY/
   THREE_SCENE/VENN/COMPLEX/FORMULA/CHART）。
2. ``_TEMPLATES_V2`` 的 THEORY 模板链含 FORMULA+DESMOS+GEOGEBRA，
   PRACTICE 模板链含 CHART+GEOMETRY。
3. LLM 路径：mock ``llm_text`` 返回含 desmos 的 JSON，断言透传不丢。
4. 学科门控：拿不到学科信息退化为始终可用；明确非数学学科时抑制数学块。
"""

from __future__ import annotations

import json

import pytest

from deeptutor.book.agents import page_planner
from deeptutor.book.agents.page_planner import SectionArchitect
from deeptutor.book.models import BlockType, Chapter, ContentType, ExplorationReport

MATH_TYPES = frozenset(
    {
        BlockType.DESMOS,
        BlockType.GEOGEBRA,
        BlockType.GEOMETRY,
        BlockType.THREE_SCENE,
        BlockType.VENN,
        BlockType.COMPLEX,
        BlockType.FORMULA,
        BlockType.CHART,
    }
)


def _chapter(content_type: ContentType = ContentType.THEORY, **extra: object) -> Chapter:
    return Chapter(
        id="ch-math-1",
        title="Quadratic functions",
        summary="Graphs, roots and the quadratic formula.",
        learning_objectives=["Sketch a parabola", "Solve quadratics"],
        content_type=content_type,
        **extra,
    )


# ── 1. 白名单 ──────────────────────────────────────────────────────────


def test_phase1_whitelist_includes_all_math_types() -> None:
    assert MATH_TYPES <= page_planner._PHASE1_TYPES


def test_llm_allowed_types_include_math_types() -> None:
    # LLM 透传路径（plan_blocks_async）也要能放行数学块，否则 mock 返回的
    # desmos 会被过滤掉（任务书验收点 3 的前提）。
    assert MATH_TYPES <= page_planner._ALLOWED_LLM_TYPES


# ── 2. 静态模板链 ──────────────────────────────────────────────────────


def test_theory_template_chain_contains_math_blocks() -> None:
    types = [bt for bt, _ in page_planner._TEMPLATES_V2[ContentType.THEORY]]
    assert BlockType.FORMULA in types
    assert BlockType.DESMOS in types
    assert BlockType.GEOGEBRA in types


def test_practice_template_chain_contains_math_blocks() -> None:
    types = [bt for bt, _ in page_planner._TEMPLATES_V2[ContentType.PRACTICE]]
    assert BlockType.CHART in types
    assert BlockType.GEOMETRY in types


# ── 3. LLM 路径透传 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_plan_passes_through_math_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_llm_json(**_: object) -> dict:
        return {
            "blocks": [
                {"type": "section", "focus": "intro", "params": {"role": "intro"}},
                {
                    "type": "desmos",
                    "focus": "parabola y=x^2",
                    "transition_in": "Now watch the curve",
                    "params": {"topic": "parabola"},
                },
                {"type": "quiz", "params": {"num_questions": 2}},
            ]
        }

    monkeypatch.setattr(page_planner, "llm_json", fake_llm_json)

    blocks = await SectionArchitect().plan_blocks_async(
        _chapter(),
        exploration=ExplorationReport(summary="Polynomials."),
        language="en",
    )

    types = [b.type for b in blocks]
    assert BlockType.DESMOS in types
    desmos = next(b for b in blocks if b.type == BlockType.DESMOS)
    assert desmos.params["topic"] == "parabola"
    assert desmos.metadata["transition_in"] == "Now watch the curve"
    # 透传不丢：数学块之外既有类型照常保留。
    assert BlockType.SECTION in types
    assert BlockType.QUIZ in types


# ── 4. 学科门控（尽力而为） ────────────────────────────────────────────


def test_math_blocks_available_without_subject_signal() -> None:
    # 拿不到学科信息 → 退化为始终可用（任务书第 4 点）。
    blocks = SectionArchitect().plan_blocks(_chapter())
    assert any(b.type == BlockType.DESMOS for b in blocks)


def test_math_blocks_suppressed_for_non_math_subject() -> None:
    blocks = SectionArchitect().plan_blocks(_chapter(subject="Chinese literature"))
    assert not any(b.type in MATH_TYPES for b in blocks)
    # 非数学块仍保留，既有块顺序语义不破坏。
    assert any(b.type == BlockType.SECTION for b in blocks)
    assert any(b.type == BlockType.QUIZ for b in blocks)


@pytest.mark.asyncio
async def test_llm_plan_drops_math_blocks_for_non_math_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_llm_json(**_: object) -> dict:
        return {
            "blocks": [
                {"type": "section", "params": {"role": "intro"}},
                {"type": "desmos", "params": {"topic": "parabola"}},
            ]
        }

    monkeypatch.setattr(page_planner, "llm_json", fake_llm_json)

    blocks = await SectionArchitect().plan_blocks_async(
        _chapter(subject="English literature"),
        language="en",
    )

    assert not any(b.type in MATH_TYPES for b in blocks)
    # 非数学 block 不受影响。
    assert any(b.type == BlockType.SECTION for b in blocks)
