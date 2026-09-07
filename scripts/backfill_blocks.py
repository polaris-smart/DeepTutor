#!/usr/bin/env python3
"""P1-B 存量书补产 — 逐页补齐缺失的生成块（backfill script）.

30 本生产书全部走 canonicalize 零 LLM 导入，页面 95% 只有 READING 原文块。
生成管线本身健康（``page_planner._TEMPLATES_V2`` 每页模板含 QUIZ / CALLOUT /
FIGURE / FLASH_CARDS，DEEP_DIVE 与错题三件生成器已就绪），只是从未对存量书
运行过。本脚本按页型模板检测每页缺失的块类型，通过 ``BookEngine.insert_block``
官方插入路径补产 —— 数据访问全部走既有 storage 层，绝不直连 sqlite / 页面 JSON。

Usage::

    # 试算（零副作用：不调 LLM、不写库），逐页列出计划补的块类型与理由
    python scripts/backfill_blocks.py --book-id bk_xxx --dry-run

    # 正式补产（LLM 并发 ≤2，429 指数退避，断点续跑）
    python scripts/backfill_blocks.py --book-id bk_xxx

    # 试点控量：先 1 本书 × 5 页
    python scripts/backfill_blocks.py --all-ready --limit-book 1 --limit-page 5

规则
----
* 页型模板：``_TEMPLATES_V2[page.content_type]``；OVERVIEW 页无模板 → 跳过。
* 学科门控：复用 ``page_planner._math_blocks_allowed`` 逻辑。章节自身带
  ``subject``/``discipline`` 时直接用；否则回退到 ``book.metadata["subject"]``
  或书名推断（``learning.ingest_pipeline._subject_of``），可用 ``--subject``
  强制指定本次运行全部书籍的学科。非数学书剔除交互族（desmos/geogebra/…），
  错题三件与 deep_dive 各科均可产。
* 长文骨架（SECTION/TEXT）不补：存量书已有 READING 原文，再补 4 段 LLM 长文
  只会与教材原文重复，按页记 note 跳过。
* 生成器未注册的块类型不插（registry 查询为准）——否则 insert_block 会留下
  永远 PENDING 的空壳。
* 错题三件走 P1-A 已有链：KP 题库优先；无错题数据时生成器 BlockSkipped，
  块被 compiler 剪掉，天然安全，批次不中断。
* 断点续跑：每页完成即追加 ``<state-dir>/backfill-<book>.jsonl``，重跑时跳过
  已完成的页（``--no-resume`` 强制重来）。
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from deeptutor.book.agents.page_planner import (  # noqa: E402
    _MATH_BLOCK_TYPES,
    _TEMPLATES_V2,
    _math_blocks_allowed,
)
from deeptutor.book.blocks.base import get_block_registry  # noqa: E402
from deeptutor.book.errors import BookPausedError  # noqa: E402
from deeptutor.book.models import (  # noqa: E402
    Block,
    BlockStatus,
    BlockType,
    Book,
    BookStatus,
    Chapter,
    Page,
)
from deeptutor.learning.ingest_pipeline import _subject_of  # noqa: E402

#: 断点续跑状态目录（相对当前工作目录）。
DEFAULT_STATE_DIR = "pipeline-state"
#: LLM 并发上限（任务书硬约束 ≤2）。
DEFAULT_CONCURRENCY = 2
#: 单块 429 退避重试次数。
DEFAULT_MAX_RETRIES = 3
#: 退避基数（秒），指数增长 5s → 10s → 20s。
DEFAULT_BACKOFF_SECONDS = 5.0

# 长文骨架：存量书已有 READING 原文块，不再补 LLM 长文（记 note 跳过）。
_PROSE_BLOCK_TYPES = frozenset({BlockType.SECTION, BlockType.TEXT})

# 视作"该类型已有内容"的块状态。PENDING/GENERATING 是欠着的工作，补了会重复。
_PRESENT_STATUSES = frozenset({BlockStatus.PENDING, BlockStatus.GENERATING, BlockStatus.READY})

# 各科均可产的补位块（不进 _TEMPLATES_V2，但生成器已就绪）。
_DEEP_DIVE_TYPE = BlockType.DEEP_DIVE
_ERROR_DIAGNOSIS_TYPE = BlockType.ERROR_DIAGNOSIS


# ─────────────────────────────────────────────────────────────────────────────
# 学科门控（复用 page_planner 逻辑）
# ─────────────────────────────────────────────────────────────────────────────


def effective_subject(chapter: Chapter, book: Book, *, subject_override: str = "") -> str:
    """Resolve the subject hint used for the math-block gate.

    章节自带的 ``subject`` / ``discipline`` extra 最可信（与 planner 同源）；
    否则回退书 metadata，再否则按书名推断（复用 ``ingest_pipeline._subject_of``，
    排除其兜底值"通用"）。返回空串表示实在拿不到学科信息，由调用方决定退路。
    """
    if subject_override.strip():
        return subject_override.strip()
    for attr in ("subject", "discipline"):
        value = str(getattr(chapter, attr, "") or "").strip()
        if value:
            return value
    metadata = book.metadata or {}
    value = str(metadata.get("subject") or "").strip()
    if value:
        return value
    inferred = _subject_of(book.title)
    return inferred if inferred and inferred != "通用" else ""


def math_blocks_allowed(chapter: Chapter, book: Book, *, subject_override: str = "") -> bool:
    """学科门控：非数学书剔除交互族。逻辑本体复用 ``_math_blocks_allowed``。"""
    subject = effective_subject(chapter, book, subject_override=subject_override)
    if not subject:
        # 拿不到学科信息时让书名自证（复用 planner 同一张非数学关键词表）：
        # 书名命中"英语/历史/语文…"即门控，否则沿用 planner 的 fail-open 放行。
        subject = book.title
    return _math_blocks_allowed(SimpleNamespace(subject=subject))


# ─────────────────────────────────────────────────────────────────────────────
# 缺失检测（纯函数，dry-run 与正式补产共用）
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class PlanItem:
    """One block the page is missing and should get."""

    block_type: BlockType
    params: dict[str, Any]
    reason: str  # template | deep_dive | error_loop


@dataclass
class PagePlan:
    """检测结果：该页补什么、跳过什么、为什么。"""

    page_id: str
    page_title: str
    content_type: str
    skip: str = ""  # 非空 = 整页跳过的原因
    planned: list[PlanItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _present_counts(page: Page) -> dict[BlockType, int]:
    counts: dict[BlockType, int] = {}
    for block in page.blocks:
        if block.status in _PRESENT_STATUSES:
            counts[block.type] = counts.get(block.type, 0) + 1
    return counts


def plan_page(
    page: Page,
    chapter: Chapter | None,
    book: Book,
    *,
    subject_override: str = "",
) -> PagePlan:
    """对照 ``_TEMPLATES_V2`` 该页型模板检测缺失的生成块。"""
    plan = PagePlan(
        page_id=page.id,
        page_title=page.title,
        content_type=page.content_type.value,
    )
    if chapter is None:
        plan.skip = "chapter_missing"
        return plan
    if page.parent_page_id:
        plan.skip = "deep_dive_subpage"
        return plan
    template = _TEMPLATES_V2.get(page.content_type)
    if template is None:
        # OVERVIEW 等无模板页：概览页本身就是确定性渲染的。
        plan.skip = "no_template"
        return plan
    if not any(block.status == BlockStatus.READY for block in page.blocks):
        # 没有任何 READY 块的页（纯空壳 / 中断残骸）归常规编译管线管，补产不掺和。
        plan.skip = "page_not_compiled"
        return plan

    math_allowed = math_blocks_allowed(chapter, book, subject_override=subject_override)
    registry = get_block_registry()
    present = _present_counts(page)

    for block_type, template_params in template:
        if block_type in _PROSE_BLOCK_TYPES:
            plan.notes.append(f"{block_type.value}: prose_backbone")
            continue
        if not math_allowed and block_type in _MATH_BLOCK_TYPES:
            plan.notes.append(f"{block_type.value}: subject_gate")
            continue
        if registry.get(block_type) is None:
            # 未注册生成器：insert 会留下永远 PENDING 的空壳，绝不插。
            plan.notes.append(f"{block_type.value}: no_generator")
            continue
        if present.get(block_type):
            plan.notes.append(f"{block_type.value}: already_present")
            continue
        params = {k: v for k, v in template_params.items() if k != "transition_in"}
        plan.planned.append(PlanItem(block_type, params, "template"))

    # 各科均可产的补位块：deep_dive（深挖卡）与 error_diagnosis（真实错题数据，
    # 无数据时生成器 BlockSkipped 静默剪块）。
    for block_type, reason in (
        (_DEEP_DIVE_TYPE, "deep_dive"),
        (_ERROR_DIAGNOSIS_TYPE, "error_loop"),
    ):
        if registry.get(block_type) is None:
            plan.notes.append(f"{block_type.value}: no_generator")
            continue
        if present.get(block_type):
            plan.notes.append(f"{block_type.value}: already_present")
            continue
        plan.planned.append(PlanItem(block_type, {}, reason))

    return plan


# ─────────────────────────────────────────────────────────────────────────────
# 断点续跑状态（pipeline-state/backfill-<book>.jsonl）
# ─────────────────────────────────────────────────────────────────────────────


def _state_path(state_dir: Path, book_id: str) -> Path:
    return state_dir / f"backfill-{book_id}.jsonl"


def load_done_page_ids(state_dir: Path, book_id: str) -> set[str]:
    path = _state_path(state_dir, book_id)
    if not path.exists():
        return set()
    done: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("page_id"):
            done.add(str(record["page_id"]))
    return done


def append_state(
    state_dir: Path,
    book_id: str,
    *,
    page_id: str,
    page_title: str,
    inserted: list[str],
    skipped: list[str],
    errors: list[str],
    dry_run: bool,
) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "book_id": book_id,
        "page_id": page_id,
        "page_title": page_title,
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors,
        "dry_run": dry_run,
    }
    with _state_path(state_dir, book_id).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# 补产执行
# ─────────────────────────────────────────────────────────────────────────────


async def _insert_with_backoff(
    engine,
    book: Book,
    page_id: str,
    item: PlanItem,
    *,
    semaphore: asyncio.Semaphore,
    max_retries: int,
    backoff_seconds: float,
    log,
) -> str:
    """Insert one block, backing off (exponentially) on rate-limit failures.

    Returns ``ready`` / ``skipped`` / ``error``. A rate-limited block is dropped
    (its ERROR shell is deleted) before the retry so the page never keeps a
    half-written duplicate.
    """
    registry = get_block_registry()
    if registry.get(item.block_type) is None:
        return "error"  # plan 阶段已拦，这里兜底。

    attempt = 0
    while True:
        block: Block | None = None
        failure: dict[str, Any] = {}
        try:
            async with semaphore:
                block = await engine.insert_block(
                    book_id=book.id,
                    page_id=page_id,
                    block_type=item.block_type,
                    params=dict(item.params),
                )
        except BookPausedError:
            log(f"[pause] {book.id} 已暂停，停止补产（BookPausedError）")
            return "paused"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — provider 层异常统一走退避
            failure = {"kind": "provider_error", "retryable": True, "message": str(exc)}
            log(f"[warn] {page_id} {item.block_type.value} insert raised: {exc}")
        if block is None and not failure:
            return "error"

        if block is not None:
            if block.status == BlockStatus.READY:
                return "ready"
            if block.status == BlockStatus.HIDDEN and (block.metadata or {}).get("skipped"):
                # BlockSkipped（如无错题数据）：块已被 compiler 剪掉，天然安全。
                return "skipped"
            failure = (block.metadata or {}).get("failure") or {}

        kind = str(failure.get("kind") or "unknown")
        if kind == "rate_limit" and attempt < max_retries:
            delay = backoff_seconds * (2**attempt)
            attempt += 1
            log(f"[backoff] {page_id} {item.block_type.value} 429 → {delay:.0f}s 后重试 {attempt}/{max_retries}")
            if block is not None:
                try:
                    await engine.delete_block(
                        book_id=book.id, page_id=page_id, block_id=block.id
                    )
                except Exception:  # noqa: BLE001 — 删失败也不影响重试
                    pass
            await asyncio.sleep(delay)
            continue

        message = str(failure.get("message") or failure.get("kind") or "unknown failure")
        log(f"[error] {page_id} {item.block_type.value} 补产失败: {message[:200]}")
        return "error"


async def _process_page(
    engine,
    book: Book,
    page: Page,
    chapter: Chapter | None,
    *,
    dry_run: bool,
    subject_override: str,
    semaphore: asyncio.Semaphore,
    max_retries: int,
    backoff_seconds: float,
    state_dir: Path,
    resume: bool,
    log,
) -> PagePlan:
    plan = plan_page(page, chapter, book, subject_override=subject_override)
    if plan.skip:
        log(f"  [skip] {page.title or plan.page_id} → {plan.skip}")
        return plan

    if dry_run:
        wanted = ", ".join(item.block_type.value for item in plan.planned) or "(无缺失)"
        log(f"  [plan] {page.title or plan.page_id} → 补 {wanted}")
        for note in plan.notes:
            log(f"         - {note}")
        # dry-run 零副作用：连断点状态文件也不写，避免污染正式跑的续跑判断。
        return plan

    if resume and plan.page_id in load_done_page_ids(state_dir, book.id):
        log(f"  [resume] {page.title or plan.page_id} 已在断点状态中，跳过")
        return plan

    inserted: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    for item in plan.planned:
        outcome = await _insert_with_backoff(
            engine,
            book,
            plan.page_id,
            item,
            semaphore=semaphore,
            max_retries=max_retries,
            backoff_seconds=backoff_seconds,
            log=log,
        )
        if outcome == "ready":
            inserted.append(item.block_type.value)
        elif outcome == "skipped":
            skipped.append(item.block_type.value)
        elif outcome == "paused":
            break
        else:
            errors.append(item.block_type.value)

    log(
        f"  [page] {page.title or plan.page_id} "
        f"补 {len(inserted)}/跳 {len(skipped)}/错 {len(errors)}"
    )
    append_state(
        state_dir,
        book.id,
        page_id=plan.page_id,
        page_title=plan.page_title,
        inserted=inserted,
        skipped=skipped,
        errors=errors,
        dry_run=False,
    )
    return plan


def resolve_subject_overrides(raw_values: list[str]) -> tuple[str, dict[str, str]]:
    """Parse ``--subject`` entries into (global, per-book) overrides.

    每个条目要么是裸学科名（作用于本次全部书），要么是 ``书id=学科``（只作用于
    那本书）。例如 ``--subject 英语 --subject bk_123=数学``。
    """
    global_subject = ""
    per_book: dict[str, str] = {}
    for raw in raw_values:
        value = raw.strip()
        if not value:
            continue
        if "=" in value:
            book_id, _, subject = value.partition("=")
            if book_id.strip() and subject.strip():
                per_book[book_id.strip()] = subject.strip()
            continue
        global_subject = value
    return global_subject, per_book


async def run_book(
    engine,
    book: Book,
    *,
    dry_run: bool,
    subject_override: str,
    concurrency: int,
    max_retries: int,
    backoff_seconds: float,
    state_dir: Path,
    resume: bool,
    limit_page: int | None,
    log,
) -> dict[str, Any]:
    """对一本书逐页补产。返回统计。"""
    spine = engine.load_spine(book.id)
    if spine is None:
        log(f"[error] 书 {book.id} 无 spine，跳过")
        return {"book_id": book.id, "status": "no_spine", "pages": 0, "planned": 0}
    chapters = {chapter.id: chapter for chapter in spine.chapters}
    pages = engine.list_pages(book.id)
    if limit_page is not None:
        pages = pages[:limit_page]

    log(f"== 书 {book.title} ({book.id}) status={book.status.value} pages={len(pages)} ==")
    semaphore = asyncio.Semaphore(max(1, concurrency))
    if not dry_run and resume:
        log(f"  断点续跑：已完成 {len(load_done_page_ids(state_dir, book.id))} 页将跳过")

    plans = await asyncio.gather(
        *(
            _process_page(
                engine,
                book,
                page,
                chapters.get(page.chapter_id),
                dry_run=dry_run,
                subject_override=subject_override,
                semaphore=semaphore,
                max_retries=max_retries,
                backoff_seconds=backoff_seconds,
                state_dir=state_dir,
                resume=resume,
                log=log,
            )
            for page in pages
        )
    )

    summary = {
        "book_id": book.id,
        "title": book.title,
        "status": "dry_run" if dry_run else "ok",
        "pages": len(plans),
        "pages_planned": sum(1 for p in plans if p.planned and not p.skip),
        "pages_skipped": sum(1 for p in plans if p.skip),
        "blocks_planned": sum(len(p.planned) for p in plans),
        "notes": sorted({note for p in plans for note in p.notes}),
    }
    log(
        f"== 小结 {book.title}: 页 {summary['pages']} / 待补页 {summary['pages_planned']} "
        f"/ 待补块 {summary['blocks_planned']} / 整页跳过 {summary['pages_skipped']} =="
    )
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P1-B 存量书补产：逐页补齐缺失的生成块（对照 _TEMPLATES_V2）。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    targets = parser.add_mutually_exclusive_group(required=True)
    targets.add_argument("--book-id", action="append", default=[], help="书 id（可多次）")
    targets.add_argument("--all-ready", action="store_true", help="处理书架上全部 READY 书")
    parser.add_argument("--dry-run", action="store_true", help="只列计划，不调 LLM、不写库")
    parser.add_argument("--limit-page", type=int, default=None, help="每本书最多处理 N 页（试点控量）")
    parser.add_argument("--limit-book", type=int, default=None, help="最多处理 N 本书（试点控量）")
    parser.add_argument(
        "--subject",
        action="append",
        default=[],
        help="学科覆盖：裸学科名（作用于本次全部书）或 书id=学科（只作用于该书），可多次；"
        "如 --subject 英语 --subject bk_123=数学",
    )
    parser.add_argument("--state-dir", default=DEFAULT_STATE_DIR, help="断点续跑状态目录")
    parser.add_argument("--no-resume", action="store_true", help="忽略已有断点状态，从头重跑")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, help="页级并发（LLM 并发 ≤2）")
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES, help="429 退避重试次数")
    parser.add_argument("--backoff-seconds", type=float, default=DEFAULT_BACKOFF_SECONDS, help="退避基数（秒）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    state_dir = Path(args.state_dir)

    from deeptutor.book.engine import get_book_engine

    engine = get_book_engine()
    books = engine.list_books()
    if not books:
        path_service = getattr(engine.storage, "path_service", None)
        book_dir = (
            path_service.get_book_dir() if path_service is not None else "未知（storage 无 path_service）"
        )
        print(f"未找到书数据（book dir: {book_dir}）——无书可补产，正常退出。")
        return 0

    if args.all_ready:
        selected = [book for book in books if book.status == BookStatus.READY]
    else:
        known = {book.id: book for book in books}
        selected = []
        missing = [book_id for book_id in args.book_id if book_id not in known]
        if missing:
            print(f"未找到书数据: {', '.join(missing)}（书架共 {len(books)} 本）")
            return 1
        selected = [known[book_id] for book_id in args.book_id]
    if args.limit_book is not None:
        selected = selected[: max(0, args.limit_book)]
    if not selected:
        print("没有符合条件的书（--all-ready 只取 READY 状态书）——无事可做。")
        return 0

    resume = not args.no_resume
    global_subject, per_book_subject = resolve_subject_overrides(args.subject)
    summaries: list[dict[str, Any]] = []
    for book in selected:
        summaries.append(
            asyncio.run(
                run_book(
                    engine,
                    book,
                    dry_run=args.dry_run,
                    subject_override=per_book_subject.get(book.id, global_subject),
                    concurrency=args.concurrency,
                    max_retries=args.max_retries,
                    backoff_seconds=args.backoff_seconds,
                    state_dir=state_dir,
                    resume=resume,
                    limit_page=args.limit_page,
                    log=print,
                )
            )
        )

    print("\n== 汇总 ==")
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
