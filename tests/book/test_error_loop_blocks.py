"""P1-A 错题闭环三件：error_diagnosis / retrieval_practice / module_test.

覆盖任务书验收点：
1. 三个生成器可实例化、已注册进 ``get_block_registry()``、双语 prompt yaml
   可加载且 user_template 可插值（不残留占位符）。
2. D4 数据注入判据：KP 题库优先（原题透传，不改写）、无错题数据时
   error_diagnosis 直接跳过、LLM fallback 的 payload 带 ``generated: true``。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from deeptutor.book.blocks._prompts import get_book_prompt, load_book_prompts
from deeptutor.book.blocks.base import (
    BlockContext,
    BlockSkipped,
    get_block_registry,
)
from deeptutor.book.blocks.error_diagnosis import ErrorDiagnosisGenerator
from deeptutor.book.blocks.module_test import ModuleTestGenerator
from deeptutor.book.blocks.retrieval_practice import (
    RetrievalPracticeGenerator,
)
from deeptutor.book.models import (
    Block,
    BlockStatus,
    BlockType,
    Chapter,
    ContentType,
    Page,
    PageStatus,
)

ERROR_LOOP_TYPES = (
    BlockType.ERROR_DIAGNOSIS,
    BlockType.RETRIEVAL_PRACTICE,
    BlockType.MODULE_TEST,
)
LANGUAGES = ("en", "zh")


def _chapter() -> Chapter:
    return Chapter(
        id="ch-el-1",
        title="一元二次方程",
        summary="配方法与求根公式。",
        learning_objectives=["会用求根公式"],
        content_type=ContentType.THEORY,
    )


def _page() -> Page:
    return Page(
        id="pg-el-1",
        book_id="bk-el",
        chapter_id="ch-el-1",
        title="一元二次方程",
    )


def _ctx(block: Block, extra: dict | None = None) -> BlockContext:
    return BlockContext(
        book_id="",
        chapter=_chapter(),
        page=_page(),
        block=block,
        language="zh",
        extra=extra or {},
    )


def _block(block_type: BlockType) -> Block:
    return Block(id=f"blk-{block_type.value}", type=block_type, status="pending")


# ── 1. 注册表 + 双语 prompt ────────────────────────────────────────────


@pytest.mark.parametrize("block_type", ERROR_LOOP_TYPES)
def test_generator_is_registered_and_instantiable(block_type: BlockType) -> None:
    generator = get_block_registry().get(block_type)
    assert generator is not None
    assert generator.block_type == block_type


@pytest.mark.parametrize("block_type", ERROR_LOOP_TYPES)
@pytest.mark.parametrize("language", LANGUAGES)
def test_prompt_bundles_load_in_both_languages(
    block_type: BlockType, language: str
) -> None:
    prompts = load_book_prompts(block_type.value, language)
    assert get_book_prompt(prompts, "system").strip()
    assert get_book_prompt(prompts, "user_template").strip()


# ── 2. KP 题库优先：原题透传，禁止模型重造 ─────────────────────────────


@pytest.mark.asyncio
async def test_retrieval_practice_uses_bank_items_verbatim() -> None:
    bank = {
        "kp_question_banks": [
            {
                "kp_id": "kp-1",
                "kp_name": "配方法",
                "module_id": "m1",
                "module_name": "二次方程",
                "items": [
                    {
                        "question": "用配方法解 x²+2x-3=0",
                        "question_type": "short",
                        "options": {},
                        "answer": "x=1 或 x=-3",
                        "explanation": "配方得 (x+1)²=4",
                        "difficulty": "medium",
                        "source": "bank",
                    }
                ],
            }
        ]
    }
    block = _block(BlockType.RETRIEVAL_PRACTICE)
    block.params = {"count": 3}
    payload, _anchors, _meta = await RetrievalPracticeGenerator()._generate(_ctx(block, bank))

    assert payload["source"] == "question_bank"
    assert "generated" not in payload
    assert payload["items"][0]["question"] == "用配方法解 x²+2x-3=0"
    assert payload["items"][0]["source"] == "bank"


@pytest.mark.asyncio
async def test_module_test_bank_items_pass_through_with_kp_scope() -> None:
    bank = {
        "kp_question_banks": [
            {
                "kp_id": "kp-1",
                "kp_name": "配方法",
                "module_id": "m1",
                "module_name": "二次方程",
                "items": [
                    {"question": "q1", "question_type": "choice", "options": {"A": "1"}, "answer": "A"},
                    {"question": "q2", "question_type": "choice", "options": {"A": "2"}, "answer": "A"},
                ],
            },
            {
                "kp_id": "kp-2",
                "kp_name": "判别式",
                "module_id": "m2",
                "module_name": "其他模块",
                "items": [
                    {"question": "q3", "question_type": "short", "options": {}, "answer": "无"}
                ],
            },
        ]
    }
    block = _block(BlockType.MODULE_TEST)
    block.params = {"num_questions": 2, "module_id": "m1"}
    payload, _anchors, _meta = await ModuleTestGenerator()._generate(_ctx(block, bank))

    assert payload["source"] == "question_bank"
    assert [q["question"] for q in payload["questions"]] == ["q1", "q2"]
    assert payload["kp_ids"] == ["kp-1"]
    assert payload["module_names"] == ["二次方程"]


# ── 3. LLM fallback 带 generated:true；error_diagnosis 无数据即跳过 ────


@pytest.mark.asyncio
async def test_retrieval_practice_llm_fallback_marks_generated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from deeptutor.book.blocks import retrieval_practice as module

    async def fake_llm_json(**_kwargs):
        return {
            "items": [
                {
                    "question": "求根公式是什么？",
                    "question_type": "short",
                    "answer": "x=(-b±√(b²-4ac))/(2a)",
                }
            ]
        }

    monkeypatch.setattr(module, "llm_json", fake_llm_json)
    block = _block(BlockType.RETRIEVAL_PRACTICE)
    payload, _anchors, _meta = await RetrievalPracticeGenerator()._generate(_ctx(block))

    assert payload["source"] == "llm"
    assert payload["generated"] is True
    assert payload["items"][0]["source"] == "generated"


@pytest.mark.asyncio
async def test_error_diagnosis_skips_without_real_error_data() -> None:
    # book_id 为空 → 两级数据源都拿不到 → 必须跳过（BlockSkipped，非 ERROR）。
    block = _block(BlockType.ERROR_DIAGNOSIS)
    with pytest.raises(BlockSkipped):
        await ErrorDiagnosisGenerator()._generate(_ctx(block))


# ── 4. 静默不落块：无错题数据时页面块列表零新增（ZC 裁决微修） ───────────


@pytest.mark.asyncio
async def test_error_diagnosis_silent_skip_marks_hidden_not_error() -> None:
    # 公开 generate() 主通道：skip 后块是 HIDDEN，绝不是 ERROR 卡。
    page = _page()
    block = _block(BlockType.ERROR_DIAGNOSIS)
    page.blocks = [block]
    ctx = BlockContext(
        book_id="",  # EvidenceStore 空：book_id 为空 → 两级数据源都拿不到
        chapter=_chapter(),
        page=page,
        block=block,
        language="zh",
    )

    result = await ErrorDiagnosisGenerator().generate(ctx)

    assert result is block
    assert block.status == BlockStatus.HIDDEN
    assert block.error == ""
    assert block.metadata["skipped"] is True
    assert block.payload == {}
    assert "failure" not in block.metadata


@pytest.mark.asyncio
async def test_error_diagnosis_silent_skip_leaves_page_block_list_empty() -> None:
    # 硬判据：无错题数据 → 页面块列表零新增（无任何块，含错误块）。
    # 走 compiler 的统一出口 _finalize_page_status（compile/regenerate/insert
    # 三条通道都经过它），块被剪掉、页状态不受拖累。
    from deeptutor.book.compiler import BookCompiler

    page = _page()
    diagnosis = _block(BlockType.ERROR_DIAGNOSIS)
    text = _block(BlockType.TEXT)
    text.status = BlockStatus.READY
    page.blocks = [text, diagnosis]
    ctx = BlockContext(
        book_id="",
        chapter=_chapter(),
        page=page,
        block=diagnosis,
        language="zh",
    )

    await ErrorDiagnosisGenerator().generate(ctx)
    BookCompiler._finalize_page_status(page)

    assert page.blocks == [text]  # 错误块消失，零新增
    assert all(b.status != BlockStatus.ERROR for b in page.blocks)
    assert page.status == PageStatus.READY  # 幸存块不受跳过拖累


@pytest.mark.asyncio
async def test_error_diagnosis_builds_payload_from_real_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from deeptutor.book.blocks import error_diagnosis as module

    rows = [
        SimpleNamespace(kp_id="kp-1", error_type="deviation", question_id="q-1"),
        SimpleNamespace(kp_id="kp-1", error_type="deviation", question_id="q-2"),
        SimpleNamespace(kp_id="kp-2", error_type="", question_id="q-3"),
    ]

    async def fake_llm_json(**_kwargs):
        return {
            "guidance": "先别急，这两类题我们一步步来。",
            "focus": [{"kp_name": "配方法", "advice": "先抄定义再做两道同类题。"}],
        }

    monkeypatch.setattr(module, "llm_json", fake_llm_json)
    monkeypatch.setattr(module, "load_kp_names", lambda _book_id: {"kp-1": "配方法"})

    block = _block(BlockType.ERROR_DIAGNOSIS)
    payload, _anchors, _meta = await ErrorDiagnosisGenerator()._generate(
        _ctx(block, {"error_evidence": rows})
    )

    assert payload["total_errors"] == 3
    assert "generated" in payload  # 模型只贡献了 coaching，需标注
    hottest = payload["diagnoses"][0]
    assert hottest["kp_id"] == "kp-1"
    assert hottest["count"] == 2
    assert hottest["advice"] == "先抄定义再做两道同类题。"


def test_bank_choice_item_without_answer_is_dropped() -> None:
    from deeptutor.book.blocks._learning_data import clean_bank_item

    assert clean_bank_item({"question": "选谁？", "question_type": "choice", "options": {"A": "1"}}) is None
    assert clean_bank_item({"question": "选谁？", "question_type": "choice", "options": {"A": "1"}, "answer": "A"})
