"""enrich() — the single synchronous entry the document_loader hook calls.

Pure-local (no network): classification rules + structure tree + QA split +
image linkage, all from the parse-cache IR. The LLM classification pass runs
separately (``classify_document_async``) as a background task and only when
rules fail to pin the subject.

Fail-open by contract: any exception here must degrade to "no metadata" —
the caller wraps this in try/except anyway, but we also guard inside so a
single malformed block never poisons the whole document.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

from .classifier import DocClassification, classify_by_llm, classify_by_rules, needs_llm
from .image_link import _image_id_v1, _image_id_v2, _text_v1, _text_v2, link_images
from .qa_split import QASplitResult, split_qa
from .structure import build_tree

logger = logging.getLogger(__name__)


def _detect_format(blocks: list[dict]) -> bool:
    """True when blocks are v2 style (title/paragraph + nested content)."""
    for block in blocks[:20]:
        if block.get("type") in ("title", "paragraph"):
            if isinstance(block.get("content"), dict):
                return True
        if isinstance(block.get("text"), str) and block.get("text"):
            return False
    return False


def enrich(
    blocks: list[dict] | None,
    markdown: str,
    filename: str,
) -> dict[str, Any]:
    """Return the doc-intel payload for one parsed document.

    Shape::

        {
          "classification": DocClassification,
          "tree": {...} | None,
          "paths": [per-block struct path],
          "qa": QASplitResult,
          "block_meta": [per-block merged metadata dict],
          "format": "v1" | "v2",
        }

    ``block_meta[i]`` is what document_loader merges into node *i*'s metadata
    (doc-level fields are replicated onto every block; per-block fields —
    struct_path, roles, image links — vary).
    """
    blocks = blocks or []
    is_v2 = _detect_format(blocks)
    text_fn = _text_v2 if is_v2 else _text_v1

    # ── ① classification (rules only here; LLM pass is async) ──
    sample = "\n".join(text_fn(b) for b in blocks[:25] if text_fn(b))[:2000]
    title = text_fn(blocks[0]) if blocks else ""
    classification = classify_by_rules(filename, first_page_text=sample, title_text=title)

    # ── ② structure tree + per-block paths ──
    try:
        tree, paths = build_tree(blocks, text_fn=text_fn)
    except Exception:
        logger.exception("doc_intel structure failed for %s", filename)
        tree, paths = None, [""] * len(blocks)

    # ── ③ QA split ──
    try:
        qa: QASplitResult = split_qa(blocks, text_fn=text_fn)
    except Exception:
        logger.exception("doc_intel qa_split failed for %s", filename)
        qa = QASplitResult(roles=[""] * len(blocks), questions={})

    # ── ④ image linkage ──
    question_blocks = {
        q["q_block_idx"]: qid
        for qid, q in qa.questions.items()
        if q.get("q_block_idx") is not None
    }
    try:
        owner_images, image_meta = link_images(
            blocks, qa.roles, question_blocks, text_fn=text_fn,
            image_id_fn=_image_id_v2 if is_v2 else _image_id_v1,
        )
    except Exception:
        logger.exception("doc_intel image_link failed for %s", filename)
        owner_images, image_meta = {}, {}

    # ── merge per-block metadata ──
    doc_md = classification.as_metadata()
    block_meta: list[dict[str, Any]] = []
    for i in range(len(blocks)):
        md: dict[str, Any] = dict(doc_md)
        if i < len(paths) and paths[i]:
            md["struct_path"] = paths[i]
        md.update(qa.as_block_metadata(i))
        if i in owner_images:
            md["linked_images"] = owner_images[i]
        if i in image_meta:
            md.update(image_meta[i])
        block_meta.append(md)

    return {
        "classification": classification,
        "tree": tree,
        "paths": paths,
        "qa": qa,
        "block_meta": block_meta,
        "format": "v2" if is_v2 else "v1",
    }


async def classify_document_async(
    filename: str,
    sample: str,
    chat_fn: Callable[[str], Awaitable[str]] | None = None,
) -> DocClassification | None:
    """LLM pass for docs whose subject the rules could not pin. Wired to the
    model gateway by the ingestion hook; returns None on failure (degrade)."""
    if chat_fn is None:
        return None
    return await classify_by_llm(filename, sample, chat_fn=chat_fn)
