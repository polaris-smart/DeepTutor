"""素材处理管线编排器 v1 — 教师自助上传全自动化（解析→建结构→题库→挂载）.

把过去四段手工脚本串成一条异步管线，带诚实状态机：每个 stage 的状态为
``queued / running / done / failed / skipped``，单段失败会落 ``failed`` 并让
后续段 ``skipped``（失败不吞、不伪成功），支持单段重试与断点幂等。

四段（吸收 DeepTutor 上游「诚实状态机」设计）::

    structured     检查书结构就绪（canonical KP 树 + spine 章节链，锚定
                   book_id 对应的书，不扫 KB 全树；无 canonical 树 fail-loud）
    qb_generated   调 LLM 从该书各章页正文（reading 块原文，含例题/习题/
                   答案与解析）提取/编制题目，产 question_banks/<book_id>.json
    kp_mapped      推导 chapter_id -> kp_id 映射（kp_mapper.build_map）
    qb_mounted     按映射逐 KP 写入 KP.meta["question_bank"]（直连 learning
                   service，不走 HTTP；契约同 mastery_path.set_kp_question_bank；
                   映射为章级 id 时经 struct_path 路径段前缀祖先回退桥接到叶级 KP）

状态仓为 JSON 文件仓（仿 :mod:`deeptutor.learning.assignments` 的 fail-open
读 / 原子写 / 单进程写锁 / 幂等 upsert），落 ``data/system/learning/
ingest_pipeline.json``；题库产物落 ``data/question_banks/``。LLM 调用走现有
provider 机制（``services.llm``），key 从配置读取，绝不 hardcode。
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import re
import threading
from typing import Any, Callable
from uuid import uuid4

from deeptutor.learning.kp_mapper import (
    DEFAULT_THRESHOLD,
    build_map,
    extract_kps,
    unwrap_tree,
)
from deeptutor.services.file_io import atomic_write_json

logger = logging.getLogger(__name__)

#: 四段顺序（状态机推进顺序）。
STAGE_ORDER = ["structured", "qb_generated", "kp_mapped", "qb_mounted"]

#: 每章出题数（对齐悦学工作区 scripts/quiz_mass_generate.py 的 n=5；该脚本不在本仓）。
QUESTIONS_PER_CHAPTER = 5

#: 每章喂进出题 prompt 的页正文总字符预算（超预算按页序截断、保留前面的页）。
PROMPT_PAGE_BUDGET = 6000

#: 单进程内串行化 run 文档的读改写（与 assignments 仓同约定）。
#: ⚠️ 部署约束：此锁与 _RUN_SEMAPHORE 均为进程级——本模块只支持单 worker
#: 部署（HK 生产即单容器单进程）；多 worker 需先给 save_run 加文件锁。
_RUNS_WRITE_LOCK = threading.Lock()

#: 后台线程上限（防教师连点把 LLM 并发打爆）。
_RUN_SEMAPHORE = threading.Semaphore(2)

#: per-run 执行互斥：同一 run 同时只允许一个执行线程。
#: QA 实锤（BUG-1）：执行线程各自持快照、save_run 全量覆盖同键——两个并发
#: retry 会把失败段用旧快照覆盖回 done（丢失更新，诚实状态机被击穿）。
#: save_run 自身的锁内重读只保跨 run 安全，同 run 必须靠这把锁串行化。
_RUN_EXEC_LOCKS: dict[str, threading.Lock] = {}
_RUN_EXEC_LOCKS_GUARD = threading.Lock()


class IngestConflict(RuntimeError):
    """同一 run 已有执行在跑时的重入冲突（router 转 409）。"""


def _acquire_run_exec(run_id: str) -> threading.Lock:
    with _RUN_EXEC_LOCKS_GUARD:
        lock = _RUN_EXEC_LOCKS.setdefault(str(run_id), threading.Lock())
    if not lock.acquire(blocking=False):
        raise IngestConflict(f"run {run_id} is already executing")
    return lock


# ---------------------------------------------------------------------------
# 状态仓（JSON 文件仓，仿 assignments.py）
# ---------------------------------------------------------------------------

def _runs_file() -> Path:
    # 每调用解析，honor 测试里 monkey-patch 的 SYSTEM_ROOT。
    from deeptutor.multi_user import paths as mu_paths

    return mu_paths.SYSTEM_ROOT / "learning" / "ingest_pipeline.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_run_id() -> str:
    return f"run_{uuid4().hex[:16]}"


def _read_runs() -> dict[str, dict[str, Any]]:
    """Fail-open 读 run 文档（绝不创建文件）。"""
    path = _runs_file()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Ingest pipeline: unreadable store %s: %s", path, exc)
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write_runs(runs: dict[str, dict[str, Any]]) -> None:
    path = _runs_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, runs)


#: 段处于 running/queued 但 updated_at 停止推进超过该时长，视为进程重启
#: 留下的僵尸 run（daemon 线程随重启蒸发光），惰性清扫为 failed。
_STALE_RUN_TIMEOUT = timedelta(minutes=30)


def _sweep_stale(record: dict[str, Any]) -> dict[str, Any]:
    """僵尸 running 自愈：非终态且 updated_at 超时的 run 标 failed（不抛）。"""
    if record.get("status") not in ("running", "queued", "partial"):
        return record
    has_active = any(
        isinstance(s, dict) and s.get("status") in ("running", "queued")
        for s in record.get("stages", {}).values()
    )
    if not has_active:
        return record
    try:
        last = datetime.fromisoformat(str(record.get("updated_at") or ""))
        if datetime.now(timezone.utc) - last <= _STALE_RUN_TIMEOUT:
            return record
    except ValueError:
        return record
    for stage in record.get("stages", {}).values():
        if isinstance(stage, dict) and stage.get("status") in ("running", "queued"):
            stage["status"] = "failed"
            stage["note"] = "interrupted by restart — retry to resume"
    record["status"] = "failed"
    record["error"] = "interrupted: backend restarted while pipeline was running"
    return save_run(record)


def get_run(run_id: str) -> dict[str, Any] | None:
    record = _read_runs().get(str(run_id or ""))
    if not isinstance(record, dict):
        return None
    return _sweep_stale(record)


def save_run(record: dict[str, Any]) -> dict[str, Any]:
    """按 run_id 幂等 upsert 一条 run。"""
    run_id = str(record.get("run_id") or new_run_id())
    stored = dict(record, run_id=run_id, updated_at=utc_now_iso())
    with _RUNS_WRITE_LOCK:
        runs = _read_runs()
        runs[run_id] = stored
        _write_runs(runs)
    return stored


def find_latest_run(kb_name: str, book_id: str) -> dict[str, Any] | None:
    """取 (kb_name, book_id) 最近一条 run（按 created_at 倒序）。"""
    matches = [
        r
        for r in _read_runs().values()
        if r.get("kb_name") == kb_name and r.get("book_id") == book_id
    ]
    if not matches:
        return None
    return _sweep_stale(sorted(matches, key=lambda r: str(r.get("created_at") or ""))[-1])


# ---------------------------------------------------------------------------
# 默认依赖（真实实现；测试通过 PipelineDeps 注入 mock）
# ---------------------------------------------------------------------------

def _default_qbanks_dir() -> Path:
    """题库产物目录 data/question_banks/（每调用解析，honor 测试 patch）。"""
    from deeptutor.multi_user import paths as mu_paths

    return mu_paths.ADMIN_WORKSPACE_ROOT / "question_banks"


def _default_load_tree(kb_name: str) -> Any:
    """从 KB 的 LlamaIndex docstore 聚合 doc_tree（list[tree_root] | None）。

    复刻 knowledge._load_kb_docstore 的读取，但不导入 god router；依赖当前用户
    ContextVar（后台线程用 copy_context 传播），未解析到 KB 时 resolve_kb 抛
    HTTPException，由 structured 段落为 failed。
    """
    from deeptutor.multi_user.knowledge_access import (
        manager_for_resource,
        resolve_kb,
    )

    resource = resolve_kb(kb_name)
    manager = manager_for_resource(resource)
    try:
        storage_dir = manager.get_rag_storage_path(resource.name)
    except ValueError:
        return None
    if not (storage_dir / "docstore.json").is_file():
        return None

    from llama_index.core.storage.docstore import SimpleDocumentStore

    docstore = SimpleDocumentStore.from_persist_dir(str(storage_dir))
    roots: list[dict[str, Any]] = []
    for node in list(docstore.docs.values()):
        metadata = getattr(node, "metadata", {}) or {}
        raw_tree = metadata.get("doc_tree")
        if not raw_tree:
            continue
        if isinstance(raw_tree, str):
            try:
                tree = json.loads(raw_tree)
            except (TypeError, ValueError):
                continue
        elif isinstance(raw_tree, dict):
            tree = raw_tree
        else:
            continue
        if isinstance(tree, dict):
            roots.append(tree)
    return roots or None


def _default_load_book(book_id: str, *, storage: Any = None) -> dict[str, Any] | None:
    """读取 book_id 对应书的出题素材（canonical 树 + spine 章节链 + 页正文）。

    出题章节锚定「这本书」本身：章节链取 spine.chapters（章 id 与 pages 的
    ``chapter_id`` 直接绑定，是唯一可靠的章节-页关联），页正文取各页的
    reading 块（保真导入每页一个 reading 块的教材原文）。书不存在返回 None，
    树/章节缺失由调用方 fail-loud——这里不做静默回退。
    """
    from deeptutor.book.models import BlockType
    from deeptutor.book.storage import BookStorage

    store = storage if storage is not None else BookStorage()
    if not store.book_exists(book_id):
        return None

    spine = store.load_spine(book_id)
    chapters = [
        {"id": chapter.id, "title": chapter.title}
        for chapter in (spine.chapters if spine else [])
        if chapter.id and chapter.title
    ]

    pages: dict[str, list[dict[str, Any]]] = {}
    # 出题原文优先用 verbatim ``reading`` 块；rebuild/canonicalize 出来的书可能
    # 只有 section/text/callout 等生成块（没有 reading），此时回退用这些块的正文，
    # 否则 qb_generated 会把整本书都判成「无正文」而只出导览题。
    fallback_types = set()
    for name in ("SECTION", "TEXT", "CALLOUT", "DEEP_DIVE"):
        block_type = getattr(BlockType, name, None)
        if block_type is not None:
            fallback_types.add(block_type)
    for page in store.list_pages(book_id):  # list_pages 已按 (order, created_at) 排序
        if not page.chapter_id:
            continue
        texts: list[str] = []
        fallback_texts: list[str] = []
        for block in page.blocks:
            body = ""
            payload = getattr(block, "payload", None)
            if isinstance(payload, dict):
                body = str(payload.get("body") or "")
            if not body:  # 未 compile 的块正文还在 params 里
                params = getattr(block, "params", None)
                if isinstance(params, dict):
                    body = str(params.get("body") or "")
            body = body.strip()
            if not body:
                continue
            if block.type == BlockType.READING:
                texts.append(body)
            elif block.type in fallback_types:
                fallback_texts.append(body)
        if not texts and fallback_texts:
            texts = fallback_texts
        pages.setdefault(page.chapter_id, []).append(
            {
                "page_id": page.id,
                "title": page.display_title or page.title,
                "text": "\n".join(texts),
            }
        )
    return {
        "canonical_tree": store.load_canonical_kp_tree(book_id),
        "chapters": chapters,
        "pages": pages,
    }


def _default_llm_complete(messages: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """走现有 provider 机制调 LLM，返回 (content, usage)。key 从配置读取。"""
    import asyncio

    from deeptutor.services.llm.config import get_llm_config
    from deeptutor.services.llm.provider_factory import get_runtime_provider

    config = get_llm_config()
    provider = get_runtime_provider(config)

    async def _call() -> Any:
        return await provider.chat(
            messages=messages,
            model=config.model,
            max_tokens=4000,
            temperature=0.7,
        )

    response = asyncio.run(_call())
    usage = dict(getattr(response, "usage", {}) or {})
    usage.setdefault("model", config.model)
    return str(getattr(response, "content", "") or ""), usage


@dataclass
class PipelineDeps:
    """可注入依赖（测试用 mock 替换 load_book / load_tree / llm_complete / 目录）。"""

    load_tree: Callable[[str], Any] = field(default=_default_load_tree)
    load_book: Callable[[str], dict[str, Any] | None] = field(default=_default_load_book)
    llm_complete: Callable[[list[dict[str, Any]]], tuple[str, dict[str, Any]]] = field(
        default=_default_llm_complete
    )
    qbanks_dir: Callable[[], Path] = field(default=_default_qbanks_dir)
    learning_root: Callable[[], Path | None] = field(default=lambda: None)


# ---------------------------------------------------------------------------
# 出题 / 解析（移植自悦学工作区 scripts/quiz_mass_generate.py——不在本仓，本文件为仓内事实源；LLM 走 provider）
# ---------------------------------------------------------------------------

_PROMPT = """你是高中{subject}老师。以下是教材《{book_title}》「{chapter}」章的原文内容（含正文、例题、习题、答案与解析）：

{source_text}

请基于以上教材原文出 {n} 道单项选择题。要求：优先从原文中的例题、习题、答案与解析提取或改编，不要凭空编造原文没有的考点；题干含具体情境或材料；四个选项有干扰性；答案与解析正确且引用教材概念术语；每题解析开头标注出处页，格式如「出处：第3页。…」。
只输出 JSON 数组：
[{{"question":"题干","options":{{"A":"…","B":"…","C":"…","D":"…"}},"answer":"A","explanation":"出处：第N页。解析（含考点说明）","difficulty":"基础|巩固|提升"}}]"""


def _chapter_source_text(pages: Any, budget: int = PROMPT_PAGE_BUDGET) -> str:
    """按页序拼接该章各页 reading 正文，总预算内截断、保留前面的页。

    每页以「【页标签】」开头，供 LLM 在解析里回填出处页；页标签取页标题
    （如「第3页」），无标题回落到页序号。
    """
    parts: list[str] = []
    used = 0
    index = 0
    for page in pages or []:
        if not isinstance(page, dict):
            continue
        text = str(page.get("text") or "").strip()
        if not text:
            continue
        index += 1
        label = str(page.get("title") or "").strip() or f"第{index}页"
        block = f"【{label}】\n{text}"
        projected = used + len(block) + (2 if parts else 0)  # 页间 "\n\n" 也计入预算
        if projected <= budget:
            parts.append(block)
            used = projected
            continue
        remaining = budget - used - (2 if parts else 0)
        if remaining <= 0:
            break
        parts.append(block[:remaining])
        break  # 预算用满，后面的页不再计入
    return "\n\n".join(parts)


def _subject_of(book_title: str) -> str:
    for subject in ("政治", "历史", "地理", "语文", "数学", "英语", "物理", "化学", "生物"):
        if subject in str(book_title):
            return subject
    return "通用"


_HTML_TAG_RE = re.compile(r"<[^>]*>")


def _strip_html(value: str) -> str:
    """剥离 HTML 标签。

    题干/解析会被学习面 MarkdownRenderer 渲染，而宿主对含 HTML 的内容
    自动走 rehype-raw 路径且未接 sanitizer——LLM 输出（可被上传材料内容
    间接操纵）若夹带 ``<img onerror=…>`` 之类标签，会形成 teacher→learner
    存储型 XSS。在入库前剥掉整条链路。
    """
    return _HTML_TAG_RE.sub("", str(value))


def _parse_questions(text: str, n: int) -> list[dict[str, Any]] | None:
    """从 LLM 输出里抽取并校验题目数组（移植 quiz_mass_generate.parse_questions，源在悦学工作区不在本仓）。"""
    text = (text or "").strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) > 1:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return None
    try:
        arr = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(arr, list):
        return None
    out: list[dict[str, Any]] = []
    for q in arr[:n]:
        if not isinstance(q, dict) or not q.get("question"):
            continue
        opts = q.get("options") or {}
        if not isinstance(opts, dict) or not all(k in opts for k in ("A", "B", "C", "D")):
            continue
        if q.get("answer") not in ("A", "B", "C", "D"):
            continue
        out.append(
            {
                "question": _strip_html(q["question"])[:1000],
                "options": {k: _strip_html(v)[:300] for k, v in opts.items()},
                "answer": q["answer"],
                "explanation": _strip_html(q.get("explanation") or "")[:1200],
                "difficulty": str(q.get("difficulty") or "巩固"),
            }
        )
    return out or None


def _root_title(tree: Any) -> str:
    """取结构树根标题作为书名（list 取第一个 dict 的 title）。"""
    node = unwrap_tree(tree)
    if isinstance(node, list):
        node = next((n for n in node if isinstance(n, dict)), None)
    if isinstance(node, dict):
        return str(node.get("title") or "")
    return ""


def _exc_summary(exc: BaseException, limit: int = 160) -> str:
    return f"{type(exc).__name__}: {str(exc)[:limit]}"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RuntimeError(f"产物缺失：{path.name}（请先运行/重试前置阶段）")
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"产物损坏：{path.name}（{exc}）")
    return data if isinstance(data, dict) else {}


def _load_book_ctx(record: dict[str, Any], ctx: dict[str, Any], deps: PipelineDeps) -> None:
    """加载 book_id 对应书的出题素材入 ctx（树 / 章节链 / 每章页正文）。

    出题章节锚定「这本书」：不再扫 KB 全树（合并 KB 下会把其他书的叶子也
    当成出题章节——单本失控 40h+ 的根因）。书没有 canonical 树或章节链时
    fail-loud 带清晰指引，绝不静默回退 KB 全树。
    """
    book_id = record["book_id"]
    book = deps.load_book(book_id)
    if not book:
        raise RuntimeError(
            f"书 {book_id} 不存在或未导入（book 仓无 manifest）——请先完成教材导入（canonicalize）再重试"
        )
    tree = unwrap_tree(book.get("canonical_tree"))
    if not isinstance(tree, dict) or not tree:
        raise RuntimeError(
            f"书 {book_id} 没有 canonical KP 树（manifest.metadata.canonical_kp_tree 缺失或无效）"
            "——请先对该教材执行 canonicalize / doc_intel 建树后重试；"
            "本管线不再回退扫描 KB 全树（合并 KB 下会把其他书的章节也当成出题章节）"
        )
    chapters = [
        {
            "id": str(chapter["id"]),
            "title": str(chapter["title"]),
            "path": str(chapter.get("path") or chapter["title"]),
        }
        for chapter in (book.get("chapters") or [])
        if isinstance(chapter, dict) and chapter.get("id") and chapter.get("title")
    ]
    if not chapters:
        raise RuntimeError(
            f"书 {book_id} 的 spine 无章节链（spine.json 缺失或 chapters 为空）"
            "——请先完成教材导入的章节分组后重试"
        )
    ctx["tree"] = tree
    ctx["chapters"] = chapters
    ctx["pages"] = book.get("pages") or {}


# ---------------------------------------------------------------------------
# 四个 stage 实现
# ---------------------------------------------------------------------------

def _stage_structured(record: dict[str, Any], ctx: dict[str, Any], deps: PipelineDeps) -> dict[str, Any]:
    _load_book_ctx(record, ctx, deps)
    node_count = len(extract_kps(ctx["tree"]))
    chapters = ctx["chapters"]
    return {
        "node_count": node_count,
        "note": (
            f"本书结构就绪：canonical 树 {node_count} 个节点 / "
            f"{len(chapters)} 个出题章节（spine，书锚定）"
        ),
    }


def _stage_qb_generated(record: dict[str, Any], ctx: dict[str, Any], deps: PipelineDeps) -> dict[str, Any]:
    book_id = record["book_id"]
    bank_path = deps.qbanks_dir() / f"{book_id}.json"
    bank_path.parent.mkdir(parents=True, exist_ok=True)

    chapters = ctx.get("chapters")
    if not chapters or ctx.get("pages") is None:
        # 断点重跑（单段 retry）时 ctx 为空：重新锚定本书加载素材。
        _load_book_ctx(record, ctx, deps)
        chapters = ctx["chapters"]

    bank: dict[str, Any] = {}
    if bank_path.exists():
        try:
            loaded = json.loads(bank_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                bank = loaded
        except (OSError, json.JSONDecodeError):
            bank = {}
    if not bank:
        bank = {
            "book_id": book_id,
            "book_title": _root_title(ctx.get("tree")) or book_id,
            "questions": [],
        }
    bank.setdefault("book_id", book_id)
    bank.setdefault("questions", [])

    done_chapters = {
        str(q.get("chapter_id"))
        for q in bank["questions"]
        if isinstance(q, dict) and q.get("chapter_id")
    }
    book_title = bank.get("book_title") or _root_title(ctx.get("tree")) or book_id
    subject = _subject_of(book_title)

    total_tokens = 0
    new_questions = 0
    errors: list[str] = []
    for chapter in chapters:
        cid = str(chapter["id"])
        if cid in done_chapters:
            continue
        source_text = _chapter_source_text(ctx.get("pages", {}).get(cid))
        if not source_text:
            source_text = "（该章暂无页正文）"
        prompt = _PROMPT.format(
            subject=subject,
            chapter=chapter["title"],
            book_title=book_title,
            n=QUESTIONS_PER_CHAPTER,
            source_text=source_text,
        )
        try:
            text, usage = deps.llm_complete([{"role": "user", "content": prompt}])
        except Exception as exc:  # 单章 LLM 失败不拖垮整段
            errors.append(f"《{chapter['title'][:16]}》LLM {_exc_summary(exc, 60)}")
            continue
        questions = _parse_questions(text, QUESTIONS_PER_CHAPTER)
        if not questions:
            errors.append(f"《{chapter['title'][:16]}》题目解析失败")
            continue
        if isinstance(usage, dict):
            try:
                total_tokens += int(usage.get("total_tokens", 0) or 0)
            except (TypeError, ValueError):
                pass  # provider 返回非数字用量时跳过统计，绝不让整段失败
            source = str(usage.get("model") or "deeptutor-llm")
        else:
            source = "deeptutor-llm"
        for q in questions:
            q.update({"chapter": chapter["title"], "chapter_id": cid, "source": source})
        bank["questions"].extend(questions)
        new_questions += len(questions)
        done_chapters.add(cid)
        atomic_write_json(bank_path, bank)  # 逐章落盘，断点续跑

    if not bank["questions"]:
        detail = f"；首错：{errors[0]}" if errors else ""
        raise RuntimeError(f"题库生成失败：{len(chapters)} 章均未产出题目{detail}")

    atomic_write_json(bank_path, bank)
    total = len(bank["questions"])
    note = (
        f"题库就绪：共 {total} 题（本轮新增 {new_questions}）/ {len(chapters)} 章；"
        f"LLM tokens={total_tokens}"
    )
    if errors:
        note += f"；{len(errors)} 章失败（{'; '.join(errors[:3])}）"
    ctx["bank_path"] = str(bank_path)
    return {"count": total, "tokens": total_tokens, "note": note}


def _stage_kp_mapped(record: dict[str, Any], ctx: dict[str, Any], deps: PipelineDeps) -> dict[str, Any]:
    book_id = record["book_id"]
    bank_path = Path(ctx.get("bank_path") or deps.qbanks_dir() / f"{book_id}.json")
    bank = _load_json(bank_path)
    tree = ctx.get("tree")
    if tree is None:
        tree = unwrap_tree(deps.load_tree(record["kb_name"]))
        ctx["tree"] = tree

    mapping, report = build_map(tree, bank, DEFAULT_THRESHOLD)
    map_path = deps.qbanks_dir() / f"{book_id}.kp_map.json"
    atomic_write_json(map_path, mapping)

    summary = report["summary"]
    note = (
        f"映射完成：KP 节点 {summary['kp_nodes']} | 章节 {summary['total_chapters']} | "
        f"映射 {summary['mapped']}（精确 {summary['exact']}/模糊 {summary['fuzzy']}/"
        f"歧义 {summary['ambiguous']}）| 未匹配 {summary['unmapped']}"
    )
    ctx["map_path"] = str(map_path)
    return {"mapped": summary["mapped"], "unmapped": summary["unmapped"], "note": note}


def _group_questions(
    bank: dict[str, Any], mapping: dict[str, str]
) -> tuple[dict[str, list[dict[str, Any]]], int, int]:
    """按 KP 分组题库（移植 kp_batch_link.parse_bank_file 的校验/分组，源在悦学工作区不在本仓）。"""
    grouped: dict[str, list[dict[str, Any]]] = {}
    skipped_no_mapping = 0
    skipped_invalid = 0
    for q in bank.get("questions", []):
        if not isinstance(q, dict):
            skipped_invalid += 1
            continue
        question = str(q.get("question") or "")
        if not question or len(question) < 5 or len(question) > 2000:
            skipped_invalid += 1
            continue
        options = q.get("options") or {}
        if not isinstance(options, dict) or len(options) == 0:
            skipped_invalid += 1
            continue
        answer = str(q.get("answer") or "")
        if not answer or len(answer) > 500:
            skipped_invalid += 1
            continue
        explanation = str(q.get("explanation") or "")
        if len(explanation) > 2000:
            skipped_invalid += 1
            continue
        chapter_id = str(q.get("chapter_id") or "")
        if not chapter_id or chapter_id not in mapping:
            skipped_no_mapping += 1
            continue
        kp_id = mapping[chapter_id]
        grouped.setdefault(kp_id, []).append(
            {
                "question": question,
                "question_type": str(q.get("question_type") or "choice"),
                "options": options,
                "answer": answer,
                "explanation": explanation,
                "difficulty": str(q.get("difficulty") or ""),
            }
        )
    return grouped, skipped_no_mapping, skipped_invalid


def _node_struct_path(tree: Any, node_id: str) -> str:
    """按 node_id 在 canonical 树里查节点，取其 struct_path（查不到/无值返回 ""）。"""
    if not isinstance(tree, dict) or not node_id:
        return ""
    if str(tree.get("node_id") or "") == node_id:
        return str(tree.get("struct_path") or "")
    for child in tree.get("children") or []:
        if isinstance(child, dict):
            found = _node_struct_path(child, node_id)
            if found:
                return found
    return ""


def _path_segments(path: Any) -> list[str]:
    """把结构路径切成段：canonical ``struct_path`` 用 ">"，历史/兼容路径可能用 "/"。"""
    return [seg for seg in re.split(r"[>/]", str(path or "")) if seg]


def _is_path_prefix(ancestor_path: str, descendant_path: str) -> bool:
    """路径段前缀判断：按 ">" / "/" 切段逐段比，不做裸字符串 startswith。

    裸前缀会让「第一章 集合」误配「第一章 集合练习」——切段后两者第二段
    不同，不会误挂；分隔符必须同时兼容 ">"（书 manifest 的 canonical
    struct_path）与 "/"（早期 KB doc_tree 的写法）。
    """
    ancestor = _path_segments(ancestor_path)
    if not ancestor:
        return False
    descendant = _path_segments(descendant_path)
    return len(descendant) >= len(ancestor) and descendant[: len(ancestor)] == ancestor


def _find_kps(progress: Any, kp_id: str, tree: Any = None) -> list[Any]:
    """在 mastery path 里定位可挂载 KP 列表。

    精确匹配（kp.id / textbook_node_id）命中即返回单个——优先于祖先回退。
    未命中时做 struct_path 路径段前缀祖先回退：kp_map 的值是 canonical 树的
    章级 node_id，而 path KP（import-from-kp-tree 建）桥接的是子叶 node_id，
    章级 id 精确匹配必落空——用映射树节点的 struct_path 判祖先关系，其全部
    后代 KP 都是可挂载目标（一章 → 多个目级 KP 时逐 KP 挂）。
    祖先也匹配不上返回 []（fail-loud 语义由调用方保底报错，不静默成功）。
    """
    for module in getattr(progress, "modules", []) or []:
        for kp in getattr(module, "knowledge_points", []) or []:
            if getattr(kp, "id", "") == kp_id or getattr(kp, "textbook_node_id", "") == kp_id:
                return [kp]
    node_path = _node_struct_path(tree, kp_id)
    if not node_path:
        return []
    matched: list[Any] = []
    for module in getattr(progress, "modules", []) or []:
        for kp in getattr(module, "knowledge_points", []) or []:
            if _is_path_prefix(node_path, str(getattr(kp, "struct_path", "") or "")):
                matched.append(kp)
    return matched


def _find_kp(progress: Any, kp_id: str, tree: Any = None) -> Any:
    """在 mastery path 里定位 KP：优先 kp.id / textbook_node_id 精确匹配，
    其次 struct_path 路径段前缀祖先回退（树节点 struct_path 可经 ``tree``
    按 node_id 查得）。返回第一个命中，找不到返回 None。"""
    found = _find_kps(progress, kp_id, tree)
    return found[0] if found else None


def _bridge_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _modules_from_canonical_tree(
    book_id: str, tree: Any
) -> list[Any]:
    """按原生 ``import-from-kp-tree`` 的规则，把 canonical 树的目级 ``mu`` 节点

    投影成 LearningModule/KP 列表（与 mastery_path router 的
    ``_modules_from_canonical_tree`` 同构，避免 pipeline 依赖 api 层）。

    规则：``type: "mu"`` 节点是 KP，挂在最近的非-mu 祖先下；该祖先（节/课/单元）
    成为模块。没有 mu 节点的树返回 ``[]``（原生导入同样不支持）。
    """
    from deeptutor.learning.models import (
        KnowledgePoint,
        KnowledgeType,
        LearningModule,
    )

    groups: list[list[Any]] = []  # [anchor_title, mu_nodes]
    last_anchor: Any = None

    def walk(node: Any, anchor: Any) -> None:
        nonlocal last_anchor
        if not isinstance(node, dict):
            return
        if node.get("type") == "mu":
            if groups and anchor is last_anchor:
                groups[-1][1].append(node)
            else:
                groups.append([str((anchor or {}).get("title") or ""), [node]])
                last_anchor = anchor
        child_anchor = anchor if node.get("type") == "mu" else node
        for child in node.get("children") or []:
            walk(child, child_anchor)

    root = tree
    if isinstance(root, list):
        tops = [n for n in root if isinstance(n, dict)]
    elif isinstance(root, dict):
        tops = list(root.get("children") or [])
    else:
        tops = []
    for child in tops:
        walk(child, None)

    modules: list[Any] = []
    for i, (anchor_title, mus) in enumerate(groups):
        module_id = f"{book_id}_m{i}"
        kps = [
            KnowledgePoint(
                id=f"{module_id}_kp{j}",
                name=_bridge_str(mu.get("title")),
                type=KnowledgeType("concept"),
                module_id=module_id,
                struct_path=_bridge_str(mu.get("struct_path")),
                textbook_node_id=_bridge_str(mu.get("node_id")),
            )
            for j, mu in enumerate(mus)
            if _bridge_str(mu.get("title"))
        ]
        if not kps:
            continue
        modules.append(
            LearningModule(
                id=module_id,
                name=anchor_title or f"Chapter {i + 1}",
                order=i,
                pass_threshold=0.7,
                knowledge_points=kps,
            )
        )
    return modules


def _spine_chapter_modules(book: dict[str, Any], book_id: str) -> list[Any]:
    """canonical 树没有目级 ``mu`` 节点时的兜底：用 spine 章链建模块（政治类教材）。

    每个 spine 章（跳过 overview/导览）一个模块、一个 KP；KP 的
    ``textbook_node_id`` 用 spine 章 id —— 与题库 ``chapter_id``、kp_map 的键一致，
    这样即使 kp_mapper 把章节映射到树节点 id，挂载段也能用章 id 候选命中。
    """
    from deeptutor.learning.models import (
        KnowledgePoint,
        KnowledgeType,
        LearningModule,
    )

    chapters = [
        c
        for c in (book.get("chapters") or [])
        if isinstance(c, dict) and c.get("id") and c.get("title")
    ]
    real = [
        c
        for c in chapters
        if not any(key in str(c["title"]) for key in ("本书导览", "目录", "导览"))
    ]
    if not real:
        return []
    modules: list[Any] = []
    for i, chapter in enumerate(real):
        module_id = f"{book_id}_m{i}"
        title = str(chapter["title"])
        kp = KnowledgePoint(
            id=f"{module_id}_kp0",
            name=title,
            type=KnowledgeType("concept"),
            module_id=module_id,
            struct_path=title,
            textbook_node_id=str(chapter["id"]),
        )
        modules.append(
            LearningModule(
                id=module_id,
                name=title,
                order=i,
                pass_threshold=0.7,
                knowledge_points=[kp],
            )
        )
    return modules


def _sync_mastery_path_from_tree(
    record: dict[str, Any], ctx: dict[str, Any], deps: PipelineDeps
) -> tuple[bool, str]:
    """挂载失败时的自愈：用 canonical 树重建 mastery path（原生能力，保守边界）。

    - 树里没有目级 mu 节点 → 不做（原生 import 同样不支持，返回 False 交由调用方报错）；
    - path 已有真实学习进度（quiz_attempts / mastery_levels 非空）→ 拒绝重建，
      绝不为了挂题抹掉学生学习数据；
    - 否则按原生 ``import-from-kp-tree`` 的方式重建模块与 KP 桥接字段。
    """
    book_id = record["book_id"]
    tree = ctx.get("tree")
    if tree is None:
        _load_book_ctx(record, ctx, deps)
        tree = ctx.get("tree")
    modules = _modules_from_canonical_tree(book_id, tree)
    fallback_note = ""
    if not modules:
        # 政治类教材：canonical 树没有 mu 节点（原生 import-from-kp-tree 直接 400），
        # 回退用 spine 章链建模块（每章一个 KP，桥接 spine 章 id）。
        book = deps.load_book(book_id)
        modules = _spine_chapter_modules(book, book_id) if book else []
        if not modules:
            return False, "canonical 树无目级 mu 节点，spine 章链也不可用，原生 mastery path 导入不可用"
        fallback_note = f"（canonical 树无 mu，已用 spine 章链兜底 {len(modules)} 模块）"

    from deeptutor.learning.service import LearningService
    from deeptutor.learning.storage import LearningStore

    root = deps.learning_root()
    store = LearningStore(root=root) if root else LearningStore()
    progress = store.load(book_id)
    if progress is not None and (
        getattr(progress, "quiz_attempts", None) or getattr(progress, "mastery_levels", None)
    ):
        return False, "mastery path 已有学习进度，拒绝重建"

    service = LearningService(store)
    progress = service.get_or_create(book_id)
    service.init_modules(progress, modules)
    progress.current_module_id = modules[0].id
    progress.current_kp_index = 0
    service.save(progress)
    return True, f"已按 canonical 树重建 mastery path（{len(modules)} 模块）{fallback_note}"


def _stage_qb_mounted(record: dict[str, Any], ctx: dict[str, Any], deps: PipelineDeps) -> dict[str, Any]:
    book_id = record["book_id"]
    qbanks_dir = deps.qbanks_dir()
    bank = _load_json(qbanks_dir / f"{book_id}.json")
    mapping = _load_json(qbanks_dir / f"{book_id}.kp_map.json")
    if not mapping:
        raise RuntimeError("KP 映射为空：kp_mapped 段未产出可挂载映射")

    tree = ctx.get("tree")
    if tree is None:
        # 断点重跑（单段 retry）时 ctx 为空：重新锚定本书加载 canonical 树，
        # 供 struct_path 祖先回退按 node_id 查章级树节点（kp_map 桥接章级 id，
        # path KP 桥接子叶 id，必须经树换算）。
        _load_book_ctx(record, ctx, deps)
        tree = ctx["tree"]

    grouped, skipped_no_map, skipped_invalid = _group_questions(bank, mapping)

    from deeptutor.learning.storage import LearningStore

    root = deps.learning_root()
    store = LearningStore(root=root) if root else LearningStore()
    #: kp_id -> 映射到它的 chapter_id 列表（挂载候选：先映射值，再章 id）。
    reverse_ids: dict[str, list[str]] = {}
    for chapter_id, mapped_id in mapping.items():
        reverse_ids.setdefault(str(mapped_id), []).append(str(chapter_id))

    def _targets_for(group_id: str, progress: Any) -> list[Any]:
        for candidate in [group_id, *reverse_ids.get(group_id, [])]:
            if not candidate:
                continue
            targets = _find_kps(progress, candidate, tree)
            if targets:
                return targets
        return []

    def _mount(progress: Any) -> tuple[int, int, list[str]]:
        mounted_kps = 0
        mounted_questions = 0
        missing: list[str] = []
        for kp_id, items in grouped.items():
            targets = _targets_for(kp_id, progress)
            if not targets:
                missing.append(kp_id)
                continue
            for target in targets:
                # 合并写：只替换 question_bank，保留 meta 里的 bank_cursor 等其它键。
                target.meta = {**(target.meta or {}), "question_bank": [dict(q) for q in items]}
                mounted_kps += 1
                mounted_questions += len(items)
        return mounted_kps, mounted_questions, missing

    progress = store.load(book_id)
    mounted_kps, mounted_questions, missing = (
        _mount(progress) if progress is not None else (0, 0, [])
    )

    sync_note = ""
    if mounted_kps == 0:
        # 自愈：canonical 树与 mastery path 不同步是 ZC 素材管线的历史缺口
        # （structured 只检查树，从未把树导入 path）。这里调用原生等价逻辑重建
        # path 后重试挂载；path 有学习进度时 sync 助手会拒绝，绝不覆盖学生数据。
        try:
            synced, why = _sync_mastery_path_from_tree(record, ctx, deps)
        except Exception as exc:  # noqa: BLE001 — 自愈失败不吞原始错误
            synced, why = False, f"自愈异常 {_exc_summary(exc)}"
        sync_note = "；" + why
        if synced:
            progress = store.load(book_id)
            mounted_kps, mounted_questions, missing = (
                _mount(progress) if progress is not None else (0, 0, missing)
            )

    if progress is None:
        raise RuntimeError(
            f"Mastery path '{book_id}' 未初始化：请先在引导学习中为该书生成/导入章节，"
            f"再重试挂载{sync_note}"
        )

    if mounted_kps == 0:
        raise RuntimeError(
            f"未能挂载任何 KP：映射的 {len(grouped)} 个 KP 均未在 mastery path 中找到"
            f"（无映射题 {skipped_no_map}）{sync_note}"
        )

    store.save(progress)
    note = (
        f"挂载完成：{mounted_kps} 个 KP / {mounted_questions} 题写入 KP.meta.question_bank；"
        f"无映射跳过 {skipped_no_map} 题，无效跳过 {skipped_invalid} 题"
    )
    if missing:
        note += f"；{len(missing)} 个 KP 未在 path 中（{', '.join(missing[:5])}）"
    note += sync_note
    return {"mounted": mounted_kps, "note": note}


_STAGE_FUNCS: dict[str, Callable[[dict[str, Any], dict[str, Any], PipelineDeps], dict[str, Any]]] = {
    "structured": _stage_structured,
    "qb_generated": _stage_qb_generated,
    "kp_mapped": _stage_kp_mapped,
    "qb_mounted": _stage_qb_mounted,
}

#: 各段除 status/note 外落库的统计字段。
_STAGE_FIELDS = {
    "structured": ("node_count",),
    "qb_generated": ("count", "tokens"),
    "kp_mapped": ("mapped", "unmapped"),
    "qb_mounted": ("mounted",),
}


# ---------------------------------------------------------------------------
# 状态机推进
# ---------------------------------------------------------------------------

def _new_stage() -> dict[str, Any]:
    return {"status": "queued", "note": ""}


def _new_record(kb_name: str, book_id: str) -> dict[str, Any]:
    now = utc_now_iso()
    return {
        "run_id": new_run_id(),
        "kb_name": kb_name,
        "book_id": book_id,
        "status": "queued",
        "stages": {name: _new_stage() for name in STAGE_ORDER},
        "error": "",
        "created_at": now,
        "updated_at": now,
    }


def _rollup_status(stages: dict[str, dict[str, Any]]) -> str:
    states = [s.get("status") for s in stages.values()]
    if any(s == "failed" for s in states):
        return "failed"
    if all(s == "done" for s in states):
        return "done"
    if any(s in ("running", "queued") for s in states):
        return "running"
    # 剩余段全部 skipped 且无段在跑/待跑：部分完成（重试单段成功后的典型态），
    # 不能报 running——没有段在执行，报了就是假进行中。
    return "partial"


def _set_stage(
    record: dict[str, Any],
    stage: str,
    status: str,
    note: str = "",
    **fields: Any,
) -> None:
    entry = record["stages"].setdefault(stage, _new_stage())
    entry["status"] = status
    if note:
        entry["note"] = note
    for key in _STAGE_FIELDS.get(stage, ()):
        if key in fields:
            entry[key] = fields[key]
    record["status"] = _rollup_status(record["stages"])
    save_run(record)


def _run_stage(
    record: dict[str, Any], stage: str, ctx: dict[str, Any], deps: PipelineDeps
) -> bool:
    """执行单段，返回是否成功。失败落 failed + note（不抛）。"""
    _set_stage(record, stage, "running")
    try:
        result = dict(_STAGE_FUNCS[stage](record, ctx, deps) or {})
    except Exception as exc:
        note = _exc_summary(exc)
        logger.warning("Ingest pipeline run %s stage %s failed: %s", record["run_id"], stage, note)
        try:
            _set_stage(record, stage, "failed", note)
            record["error"] = f"{stage}: {note}"
            save_run(record)
        except OSError:
            logger.exception("Ingest pipeline run %s: failed to persist failure state", record["run_id"])
        return False
    note = str(result.pop("note", ""))
    # 终态落库包 OSError：磁盘故障若在此逃逸会杀死后台线程，段永远停在 running。
    try:
        _set_stage(record, stage, "done", note, **result)
        # 重试成功后清掉旧错误，避免状态翻 done/partial 而 error 残留误导前端。
        record.pop("error", None)
        save_run(record)
    except OSError:
        logger.exception("Ingest pipeline run %s: failed to persist done state", record["run_id"])
    return True


def _execute_all(
    record: dict[str, Any], deps: PipelineDeps, exec_lock: threading.Lock
) -> None:
    """顺次执行四段：某段失败 → 该段 failed、后续段 skipped（失败传播）。"""
    ctx: dict[str, Any] = {}
    try:
        with _RUN_SEMAPHORE:
            for index, stage in enumerate(STAGE_ORDER):
                ok = _run_stage(record, stage, ctx, deps)
                if not ok:
                    for later in STAGE_ORDER[index + 1 :]:
                        _set_stage(record, later, "skipped", f"前置阶段 {stage} 失败，跳过")
                    record["status"] = "failed"
                    save_run(record)
                    return
        record["status"] = "done"
        save_run(record)
    finally:
        exec_lock.release()


def _execute_one(
    record: dict[str, Any], stage: str, deps: PipelineDeps, exec_lock: threading.Lock
) -> None:
    """重试单段：只重跑指定段（产物幂等），其余段状态保持不变。"""
    ctx: dict[str, Any] = {}
    try:
        with _RUN_SEMAPHORE:
            _run_stage(record, stage, ctx, deps)
        record["status"] = _rollup_status(record["stages"])
        save_run(record)
    finally:
        exec_lock.release()


def _spawn(fn: Callable[[], None]) -> None:
    """后台线程执行；copy_context 传播当前用户 ContextVar（KB 解析 / 学习仓）。"""
    ctx = contextvars.copy_context()

    def _target() -> None:
        try:
            ctx.run(fn)
        except Exception:
            logger.exception("Ingest pipeline background task crashed")

    threading.Thread(target=_target, daemon=True).start()


# ---------------------------------------------------------------------------
# 公共入口
# ---------------------------------------------------------------------------

def start_pipeline(
    kb_name: str,
    book_id: str,
    *,
    deps: PipelineDeps | None = None,
    background: bool = True,
) -> dict[str, Any]:
    """创建 run 并后台顺次执行四段；background=False 时同步执行（测试用）。"""
    record = save_run(_new_record(kb_name, book_id))
    resolved = deps or PipelineDeps()
    # 新 run_id 全局唯一，锁必可得；发起线程持有、执行线程 finally 释放，
    # 保证"检查-执行"间隙内不可能出现第二个同 run 执行者。
    exec_lock = _acquire_run_exec(record["run_id"])
    if background:
        _spawn(lambda: _execute_all(record, resolved, exec_lock))
    else:
        _execute_all(record, resolved, exec_lock)
    return get_run(record["run_id"]) or record


def retry_stage(
    run_id: str,
    stage: str,
    *,
    deps: PipelineDeps | None = None,
    background: bool = True,
) -> dict[str, Any] | None:
    """重试指定段；run 不存在返回 None，同 run 已在执行抛 IngestConflict。"""
    record = get_run(run_id)
    if record is None:
        return None
    if stage not in _STAGE_FUNCS:
        raise ValueError(f"unknown stage: {stage}")
    resolved = deps or PipelineDeps()
    # 在发起线程抢 per-run 锁：/router 可把 IngestConflict 转 409，
    # 堵死"守卫检查与执行之间"的双 retry 竞态窗口（QA BUG-1）。
    # 段落 running 由 _run_stage 进入信号量后落库——排队期不假报 running。
    exec_lock = _acquire_run_exec(run_id)
    if background:
        _spawn(lambda: _execute_one(record, stage, resolved, exec_lock))
    else:
        _execute_one(record, stage, resolved, exec_lock)
    return get_run(run_id) or record
