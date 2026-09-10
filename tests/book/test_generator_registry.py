"""P1-C: 8 个数学交互生成器必须在 ``get_block_registry()`` 注册表内.

背景：chart/complex/desmos/formula/geogebra/geometry/three_scene/venn 的生成器类
早已存在于 ``deeptutor/book/blocks/``（走 ``_math_base.MathBlockGenerator`` 骨架），
却从未注册进 ``_build_default_registry``，导致存量书补产计划里这些类型全部
``no_generator`` 跳过。本测试防"类在注册表外"复发：每型断言

1. 已注册且 ``get()`` 拿到的实例类型正确（可实例化）；
2. 对应双语 prompt yaml（zh+en）存在且 ``system`` / ``user_template`` 可加载，
   ``user_template`` 能被数学骨架的四个占位符完整插值（不残留 ``{}``）；
3. mock LLM 干跑 ``_generate`` 不抛异常且 payload 非空。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from deeptutor.book.blocks import _math_base
from deeptutor.book.blocks import (
    chart as chart_mod,
)
from deeptutor.book.blocks import (
    complex as complex_mod,
)
from deeptutor.book.blocks import (
    desmos as desmos_mod,
)
from deeptutor.book.blocks import (
    formula as formula_mod,
)
from deeptutor.book.blocks import (
    geogebra as geogebra_mod,
)
from deeptutor.book.blocks import (
    geometry as geometry_mod,
)
from deeptutor.book.blocks import (
    three_scene as three_scene_mod,
)
from deeptutor.book.blocks import (
    venn as venn_mod,
)
from deeptutor.book.blocks._prompts import get_book_prompt, load_book_prompts
from deeptutor.book.blocks.base import BlockContext, get_block_registry
from deeptutor.book.models import (
    Block,
    BlockStatus,
    BlockType,
    Chapter,
    ContentType,
    Page,
    PageStatus,
)

# (BlockType, 生成器类, prompt bundle 名)
MATH_GENERATOR_CASES = (
    (BlockType.CHART, chart_mod.ChartGenerator, "chart"),
    (BlockType.COMPLEX, complex_mod.ComplexGenerator, "complex"),
    (BlockType.DESMOS, desmos_mod.DesmosGenerator, "desmos"),
    (BlockType.FORMULA, formula_mod.FormulaGenerator, "formula"),
    (BlockType.GEOGEBRA, geogebra_mod.GeoGebraGenerator, "geogebra"),
    (BlockType.GEOMETRY, geometry_mod.GeometryGenerator, "geometry"),
    (BlockType.THREE_SCENE, three_scene_mod.ThreeSceneGenerator, "three_scene"),
    (BlockType.VENN, venn_mod.VennGenerator, "venn"),
)

LANGUAGES = ("en", "zh")


# ── 关1：已注册 + 可实例化 ─────────────────────────────────────────────


@pytest.mark.parametrize("block_type,cls,_prompt", MATH_GENERATOR_CASES, ids=lambda v: getattr(v, "value", v))
def test_math_generator_is_registered_and_instantiable(
    block_type: BlockType, cls: type, _prompt: str
) -> None:
    generator = get_block_registry().get(block_type)
    assert generator is not None, f"{block_type.value} 未注册进 get_block_registry()"
    assert isinstance(generator, cls)
    assert generator.block_type == block_type


# ── 关2：双语 prompt yaml 存在、可加载、可插值 ─────────────────────────


@pytest.mark.parametrize("block_type,_cls,prompt_name", MATH_GENERATOR_CASES, ids=lambda v: getattr(v, "value", v))
@pytest.mark.parametrize("language", LANGUAGES)
def test_math_prompt_yaml_loads_and_interpolates(
    block_type: BlockType, _cls: type, prompt_name: str, language: str
) -> None:
    prompts = load_book_prompts(prompt_name, language)
    assert get_book_prompt(prompts, "system").strip(), f"{prompt_name}/{language} 缺 system"
    template = get_book_prompt(prompts, "user_template")
    rendered = template.format(
        chapter_title="数列",
        chapter_summary="等差数列与等比数列。",
        objectives_inline="掌握通项公式",
        focus="等差数列求和",
    )
    assert "{" not in rendered, f"{prompt_name}/{language} 残留未填占位符"


# ── 关3：mock LLM 干跑 _generate 不抛异常、payload 非空 ────────────────


def _fake_llm_json() -> dict[str, Any]:
    """一个能喂饱 8 个 build_payload 的超集 mock（多余键被各自裁剪）。"""
    return {
        "title": "数列示例",
        "description": "等差数列求和的交互演示。",
        "expressions": ["a_{n}=a_{1}+(n-1)d"],
        "formulas": [{"tex": "S_{n}=\\frac{n(a_1+a_n)}{2}", "caption": "求和公式"}],
        "materialId": "",
        "commands": ["A=(0,0)", "B=(4,0)"],
        "elements": [{"type": "point", "params": [1, 2], "attrs": {}}],
        "sets": [{"label": "A"}, {"label": "B"}],
        "re": 3,
        "im": 2,
        "hint": "拖动滑块观察图像变化。",
        "series": [{"name": "前n项和", "type": "bar", "data": [1, 3, 6]}],
        "categories": ["n=1", "n=2", "n=3"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("block_type,cls,_prompt", MATH_GENERATOR_CASES, ids=lambda v: getattr(v, "value", v))
async def test_math_generator_dry_run_produces_payload(
    block_type: BlockType,
    cls: type,
    _prompt: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_llm_json(**_kwargs: Any) -> dict[str, Any]:
        return _fake_llm_json()

    monkeypatch.setattr(_math_base, "llm_json", fake_llm_json)

    block = Block(id=f"blk-{block_type.value}", type=block_type, status=BlockStatus.PENDING)
    block.params = {"topic": "等差数列求和"}
    ctx = BlockContext(
        book_id="bk",
        chapter=Chapter(
            id="ch-1",
            title="第四章 数列",
            summary="等差数列与等比数列。",
            learning_objectives=["掌握通项公式"],
            content_type=ContentType.THEORY,
        ),
        page=Page(
            id="pg-1",
            book_id="bk",
            chapter_id="ch-1",
            title="4.1 数列的概念",
            status=PageStatus.READY,
        ),
        block=block,
        language="zh",
    )

    payload, _anchors, _metadata = await cls()._generate(ctx)
    assert payload, f"{block_type.value} 干跑 payload 为空"
