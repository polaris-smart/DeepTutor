"""教材全自动化管线（P7）—— upload→parse→canonicalize→import 事件链.

老师把教材 PDF 丢进一个开了 ``auto_pipeline`` 开关的 KB，upload 处理任务
成功的尾部自动接力剩下的全部环节::

    raw（upload 已落 raw/）
      → parsed      ParseService.parse（内容寻址缓存，命中即复用）
      → canonicalized  /books/canonicalize（页脚法 layout_json 优先，
                       0 章降级走 enrich 树转 toc —— 附录降级表）
      → imported    import-from-book（canonical KP 树优先 → 目级模块）

全程无人工接力。每段状态落在 KB manifest（kb_config.json 里该 KB 条目的
``metadata`` 字典）的 ``pipeline`` 键下，按文件名各存一份；状态端点
``GET /knowledge-bases/{kb}/pipeline`` 直接读它，不做二次推断。

两个契约：

* **显式开关**：只有 ``manifest.metadata.auto_pipeline`` 为真才触发。存量
  KB 缺省为假，上传行为与现状逐字节一致（回归红线）。
* **失败隔离**：任一段失败只把该文件的状态置 ``error`` + 原因，不中断同批
  其他文件，也绝不把 upload 任务本身拖成失败。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from deeptutor.knowledge.manager import KnowledgeBaseManager

logger = logging.getLogger(__name__)

#: KB manifest（kb_config.json 条目）里打开事件链的开关键。
AUTO_PIPELINE_FLAG = "auto_pipeline"
#: KB manifest.metadata 里挂每文件管线状态的键。
PIPELINE_KEY = "pipeline"

#: 状态端点按此顺序返回四段；前两段由 upload/parse 写入，后两段由本模块写入。
STAGE_KEYS = ("raw", "parsed", "canonicalized", "imported")

LogFn = Callable[..., None]


# ─────────────────────────────────────────────────────────────────────────────
# KB manifest（kb_config.json 条目）读写
# ─────────────────────────────────────────────────────────────────────────────


def _manager_for(base_dir: str | Path | None) -> KnowledgeBaseManager:
    """Resolve the KB manager for ``base_dir``.

    The upload task passes its own ``base_dir`` through; tests inject a temp
    dir the same way. ``None`` falls back to the current workspace's KB root
    (multi-user aware, like every other consumer of the manager).
    """
    if base_dir is not None:
        return KnowledgeBaseManager(base_dir=str(base_dir))
    from deeptutor.multi_user.knowledge_access import current_kb_manager

    return current_kb_manager()


def read_kb_metadata(kb_name: str, *, base_dir: str | Path | None = None) -> dict[str, Any]:
    """The KB manifest's ``metadata`` dict — ``{}`` when absent/unreadable."""
    try:
        manager = _manager_for(base_dir)
        manager.config = manager._load_config()
        entry = manager.config.get("knowledge_bases", {}).get(kb_name) or {}
        metadata = entry.get("metadata")
        return dict(metadata) if isinstance(metadata, dict) else {}
    except Exception:  # noqa: BLE001 — a broken manifest reads as "no state"
        logger.warning("auto pipeline: unreadable manifest for KB %s", kb_name, exc_info=True)
        return {}


def auto_pipeline_enabled(kb_name: str, *, base_dir: str | Path | None = None) -> bool:
    """True only when the KB manifest explicitly turns the chain on.

    Missing flag, ``false``, or a truthy-looking string like ``"false"`` all
    read as off — the default must stay off so existing KBs keep their exact
    current upload behaviour.
    """
    return bool(read_kb_metadata(kb_name, base_dir=base_dir).get(AUTO_PIPELINE_FLAG) is True)


def read_pipeline_state(
    kb_name: str, *, base_dir: str | Path | None = None
) -> dict[str, dict[str, Any]]:
    """Every file's pipeline record written so far, keyed by file name."""
    pipeline = read_kb_metadata(kb_name, base_dir=base_dir).get(PIPELINE_KEY)
    return dict(pipeline) if isinstance(pipeline, dict) else {}


def record_stage(
    kb_name: str,
    filename: str,
    stage: str,
    *,
    payload: dict[str, Any] | None = None,
    error: str | None = None,
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Persist one stage's outcome for ``filename`` into the KB manifest.

    On success the stage slot carries a timestamp plus whatever the caller
    produced (book id, parse workdir, module count…). On failure the whole
    file's record flips to ``status: error`` with the reason, leaving the
    stages that already succeeded intact for the status endpoint to show.
    """
    manager = _manager_for(base_dir)
    manager.config = manager._load_config()
    entry = manager.config.setdefault("knowledge_bases", {}).setdefault(
        kb_name, {"path": kb_name}
    )
    pipeline = entry.setdefault("metadata", {}).setdefault(PIPELINE_KEY, {})
    record = pipeline.get(filename) if isinstance(pipeline.get(filename), dict) else {}
    record = {"file": filename, **record}
    now = datetime.now().isoformat()
    record["updated_at"] = now
    if error is not None:
        record["status"] = "error"
        record["error"] = str(error)[:500]
        record[f"{stage}_error"] = str(error)[:500]
    else:
        record["status"] = "ok"
        record.pop("error", None)
        record[stage] = {"at": now, **(payload or {})}
    pipeline[filename] = record
    manager._save_config()
    return record


# ─────────────────────────────────────────────────────────────────────────────
# canonicalize 输入构建（wave2-batch / tier-rebuild-all 补丁脚本的产品化）
# ─────────────────────────────────────────────────────────────────────────────


class AutoPipelineError(RuntimeError):
    """One stage of the chain failed — the message lands in the file's state."""


def _clean_title(stem: str) -> str:
    """The book title from the PDF stem: ``人教A版2019-必修第一册`` → spaces.

    Mirrors the batch drivers: hyphens were name-mangling, not prose.
    """
    title = stem.strip()
    if "人教A版2019-" in title:
        title = title.replace("人教A版2019-", "").replace("-", " ")
    return title.replace("-", " ").strip() or stem


def _book_dir_of(workdir: str | Path) -> Path:
    """The parse product dir: ``sig/<stem>/`` layer, else the workdir itself."""
    workdir = Path(workdir)
    subdirs = [d for d in workdir.iterdir() if d.is_dir()] if workdir.is_dir() else []
    candidate = subdirs[0] if len(subdirs) == 1 and any(subdirs[0].glob("*.md")) else workdir
    if not any(candidate.glob("*.md")):
        raise AutoPipelineError(f"parse 产物目录无 md: {candidate}")
    return candidate


def _layout_from(book_dir: Path) -> dict[str, Any] | None:
    """The MinerU layout dict: whole-book ``layout.json``, or part files merged.

    Part splits (大部头) each carry their own ``pdf_info`` — renumber the
    pages into one sequence so the 页脚法 sees a whole book.
    """
    whole = book_dir / "layout.json"
    if whole.is_file():
        try:
            layout = json.loads(whole.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return layout if isinstance(layout, dict) else None

    parts = sorted(book_dir.glob("part*_layout.json"))
    if not parts:
        return None
    first: dict[str, Any] | None = None
    pages: list[dict[str, Any]] = []
    for part in parts:
        try:
            payload = json.loads(part.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if first is None and isinstance(payload, dict):
            first = {k: v for k, v in payload.items() if k != "pdf_info"}
        for page in payload.get("pdf_info") or []:
            page["page_idx"] = len(pages)
            pages.append(page)
    if first is None or not pages:
        return None
    first["pdf_info"] = pages
    return first


def _tree_to_toc(children: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Convert a doc_intel tree into the canonicalize endpoint's TOC shape.

    The 附录降级表's fallback: 页脚法 0 章（教师用书/异版式）时把 enrich 树转
    toc 建书 —— 可能无页内容，status 如实。
    """

    def convert(node: dict[str, Any]) -> dict[str, Any] | None:
        title = str(node.get("title") or "").strip()
        if not title:
            return None
        item: dict[str, Any] = {"title": title}
        kids = [
            converted
            for child in node.get("children") or []
            if isinstance(child, dict) and (converted := convert(child)) is not None
        ]
        if kids:
            item["children"] = kids
        return item

    roots = [
        converted
        for unit in children or []
        if isinstance(unit, dict) and (converted := convert(unit)) is not None
    ]
    return roots


def _page_specs_from_layout(
    layout: dict[str, Any], chapters: list[Any], title: str
) -> list[dict[str, Any]]:
    """Verbatim per-page reading blocks under each 页脚法 chapter.

    Same shape the batch drivers posted: one spec per rebuilt chapter, one
    page per ``页N``, body = the page's own text joined by blank lines.
    """
    from deeptutor.textbook_struct.chapter_rebuild import block_text

    pages = layout.get("pdf_info") or []

    def page_text(page: dict[str, Any]) -> str:
        parts = []
        for block in page.get("para_blocks") or []:
            if not isinstance(block, dict) or block.get("type") == "image":
                continue
            text = block_text(block)
            if text:
                parts.append(text)
        return "\n\n".join(parts)

    specs: list[dict[str, Any]] = []
    for index, chapter in enumerate(chapters):
        specs_pages: list[dict[str, Any]] = []
        for pno in range(chapter.page_idx + 1, (chapter.end_page_idx or 0) + 1):
            if pno >= len(pages):
                break
            text = page_text(pages[pno])
            if not text.strip():
                continue
            specs_pages.append(
                {
                    "title": f"页{pno + 1}",
                    "blocks": [
                        {
                            "block_type": "reading",
                            "params": {
                                "body": text,
                                "variant": "prose",
                                "source_label": f"{title} p{pno + 1}",
                            },
                        }
                    ],
                }
            )
        if specs_pages:
            specs.append({"chapter_index": index, "pages": specs_pages})
    return specs


def _enrich_tree(book_dir: Path, filename: str) -> dict[str, Any] | None:
    """The doc_intel tree rebuilt from the parse product's content_list."""
    blocks: list[dict[str, Any]] = []
    for cl in sorted(book_dir.glob("*_content_list.json")):
        if cl.name.endswith("_v2.json"):
            continue
        try:
            payload = json.loads(cl.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, list):
            blocks.extend(item for item in payload if isinstance(item, dict))
    if not blocks:
        return None
    from deeptutor.knowledge.doc_intel.enrich import enrich

    markdown = ""
    md = next(iter(book_dir.glob("*.md")), None)
    if md is not None:
        markdown = md.read_text(encoding="utf-8")[:5000]
    try:
        result = enrich(blocks, markdown=markdown, filename=filename, doc_id="")
    except Exception:  # noqa: BLE001 — the tree is an optimization, never required
        logger.warning("auto pipeline: enrich failed for %s", filename, exc_info=True)
        return None
    tree = result.get("tree")
    return tree if isinstance(tree, dict) and tree.get("children") else None


# ─────────────────────────────────────────────────────────────────────────────
# 事件链本体
# ─────────────────────────────────────────────────────────────────────────────


async def _canonicalize_stage(
    kb_name: str, path: Path, parsed: Any, *, book_storage: Any, canonicalize: Any
) -> dict[str, Any]:
    """canonicalize one parsed textbook: 页脚法优先，0 章降级 enrich 树转 toc.

    ``canonicalize`` is the ``/books/canonicalize`` endpoint callable (plus its
    ``CanonicalizeRequest`` model), injected by the adapter that kicks the
    chain off — domain code here never imports the API layer.
    """
    from deeptutor.book.canonical_tree import canonical_tree_from_doc_trees

    canonicalize_book, CanonicalizeRequest = canonicalize
    book_dir = _book_dir_of(parsed.workdir)
    title = _clean_title(path.stem)
    layout = _layout_from(book_dir)

    specs: list[dict[str, Any]] = []
    toc: list[dict[str, Any]] = []
    if layout is not None:
        # 页脚法: chapters from the running headers, pages from their ranges.
        from deeptutor.textbook_struct.page_headers import rebuild_from_headers

        chapters = await asyncio.to_thread(rebuild_from_headers, layout)
        specs = await asyncio.to_thread(_page_specs_from_layout, layout, chapters, title)

    if specs:
        request = CanonicalizeRequest(
            title=title,
            source="layout_json",
            layout=layout,
            language="zh",
            knowledge_bases=[kb_name],
            metadata={"origin": "auto_pipeline", "parse_dir": str(book_dir)},
            chapters=specs,
        )
        source = "layout_json"
    else:
        # 降级表: 页脚法 0 章（或无 layout）→ enrich 树转 toc 建书。
        tree = await asyncio.to_thread(_enrich_tree, book_dir, path.name)
        toc = _tree_to_toc((tree or {}).get("children"))
        if not toc:
            raise AutoPipelineError("页脚法 0 章且 enrich 树转 toc 为空")
        request = CanonicalizeRequest(
            title=title,
            source="toc_json",
            toc=toc,
            language="zh",
            knowledge_bases=[kb_name],
            metadata={"origin": "auto_pipeline", "parse_dir": str(book_dir)},
            chapters=[],
        )
        source = "toc_json"

    result = await canonicalize_book(request)
    book = result.get("book") or {}

    # canonical 树兜底落缓: the P0 hook inside canonicalize reads the KB
    # docstore's doc_tree; a KB whose ingest never stamped it (or a toc-only
    # import) gets the tree rebuilt here from the parse product instead.
    tree = None
    if source == "toc_json":
        tree = await asyncio.to_thread(_enrich_tree, book_dir, path.name)
    if tree is not None:
        slim = canonical_tree_from_doc_trees([tree])
        if slim is not None and not book_storage.load_canonical_kp_tree(book.get("id") or ""):
            book_storage.save_canonical_kp_tree(book.get("id") or "", slim)

    return {
        "book_id": book.get("id"),
        "title": book.get("title"),
        "source": source,
        "chapters": book.get("chapter_count", 0),
        "pages": book.get("page_count", 0),
        "book_status": str(getattr(book.get("status"), "value", book.get("status") or "")),
    }


async def run_auto_pipeline(
    kb_name: str,
    file_paths: list[str | Path],
    *,
    base_dir: str | Path | None = None,
    task_id: str | None = None,
    log: LogFn | None = None,
    canonicalize: Any | None = None,
    import_from_book: Any | None = None,
    import_request_model: Any | None = None,
) -> dict[str, dict[str, Any]]:
    """Run the full chain for each uploaded file, one failure at a time.

    Called at the tail of a successful upload task; every stage outcome is
    persisted via :func:`record_stage` before the next file starts, so the
    status endpoint reflects reality even mid-run or after a crash.

    ``canonicalize`` / ``import_from_book`` / ``import_request_model`` are the
    adapter endpoints this chain composes (canonicalize endpoint + request
    model, import-from-book endpoint + request model); they are injected by
    the caller in the API layer so this module stays free of adapter imports.
    """
    from deeptutor.book.storage import get_book_storage
    from deeptutor.services.parsing.service import ParseService

    if canonicalize is None or import_from_book is None or import_request_model is None:
        raise AutoPipelineError(
            "run_auto_pipeline requires the canonicalize/import-from-book "
            "endpoint callables (inject them from the API layer)"
        )

    def say(message: str, level: str = "info") -> None:
        if log is not None:
            log(message, level=level)
        elif level in ("error", "warning"):
            getattr(logger, "warning" if level == "warning" else "error")(message)
        else:
            logger.info(message)

    parse_service = ParseService()
    book_storage = get_book_storage()
    results: dict[str, dict[str, Any]] = {}

    for raw_path in file_paths:
        path = Path(raw_path)
        name = path.name
        record_stage(kb_name, name, "raw", payload={"path": str(path)}, base_dir=base_dir)
        results[name] = {"status": "ok"}

        # ── parsed: ParseService 复用缓存，产物缺失才真正解析 ──
        try:
            parsed = await asyncio.to_thread(parse_service.parse, path)
        except Exception as exc:  # noqa: BLE001
            record_stage(kb_name, name, "parsed", error=str(exc), base_dir=base_dir)
            results[name] = {"status": "error", "stage": "parsed", "error": str(exc)}
            say(f"{name}: parse failed — {exc}", level="error")
            continue
        record_stage(
            kb_name,
            name,
            "parsed",
            payload={"workdir": str(parsed.workdir), "engine": parsed.engine},
            base_dir=base_dir,
        )

        # ── canonicalized: 页脚法 layout_json → 降级 toc_json ──
        try:
            canonicalized = await _canonicalize_stage(
                kb_name, path, parsed, book_storage=book_storage, canonicalize=canonicalize
            )
        except Exception as exc:  # noqa: BLE001
            record_stage(kb_name, name, "canonicalized", error=str(exc), base_dir=base_dir)
            results[name] = {"status": "error", "stage": "canonicalized", "error": str(exc)}
            say(f"{name}: canonicalize failed — {exc}", level="error")
            continue
        record_stage(kb_name, name, "canonicalized", payload=canonicalized, base_dir=base_dir)
        say(
            f"{name}: canonicalized → {canonicalized['book_id']} "
            f"(source={canonicalized['source']} chapters={canonicalized['chapters']} "
            f"pages={canonicalized['pages']})"
        )

        # ── imported: canonical 树优先的 import-from-book ──
        book_id = str(canonicalized.get("book_id") or "")
        try:
            imported = await import_from_book(book_id, import_request_model(chapters=[]))
        except Exception as exc:  # noqa: BLE001
            record_stage(kb_name, name, "imported", error=str(exc), base_dir=base_dir)
            results[name] = {"status": "error", "stage": "imported", "error": str(exc)}
            say(f"{name}: import-from-book failed — {exc}", level="error")
            continue
        record_stage(
            kb_name,
            name,
            "imported",
            payload={"book_id": book_id, "module_count": imported.get("module_count", 0)},
            base_dir=base_dir,
        )
        say(f"{name}: imported {imported.get('module_count', 0)} module(s) into {book_id}")

    return results
