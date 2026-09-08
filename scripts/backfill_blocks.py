#!/usr/bin/env python3
"""P1-B 存量书补产 — CLI 薄壳（过渡保留，能力已入 DT 服务层）。

**单一真源在 ``deeptutor/book/backfill.py``**：缺失检测、学科门控、429 退避、
断点续跑、insert_block 官方路径全部在服务层；本文件只保留 argparse 与多书循环，
逻辑零拷贝。P1-E 后 API 端点 ``POST /books/{book_id}/backfill-blocks`` 走同一
服务层，脚本与端点行为一致。后续维护请改服务层，勿在此处加逻辑。

Usage::

    # 试算（零副作用：不调 LLM、不写库），逐页列出计划补的块类型与理由
    python scripts/backfill_blocks.py --book-id bk_xxx --dry-run

    # 正式补产（LLM 并发 ≤2，429 指数退避，断点续跑）
    python scripts/backfill_blocks.py --book-id bk_xxx

    # 试点控量：先 1 本书 × 5 页
    python scripts/backfill_blocks.py --all-ready --limit-book 1 --limit-page 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 服务层单一真源 —— 本壳只 re-export 既有名字供旧调用方/测试引用。
from deeptutor.book.agents.page_planner import (  # noqa: E402, F401
    _MATH_BLOCK_TYPES,
    _TEMPLATES_V2,
)
from deeptutor.book.backfill import (  # noqa: E402, F401
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_CONCURRENCY,
    DEFAULT_MAX_RETRIES,
    STATE_DIR_NAME as DEFAULT_STATE_DIR,
    effective_subject,
    load_done_page_ids,
    math_blocks_allowed,
    plan_page,
    resolve_subject_overrides,
    run_book,
)
from deeptutor.book.models import BookStatus  # noqa: E402


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
