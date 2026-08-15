"""
Exam Paper Assembly API Router (组卷)
=====================================

Assembles a downloadable ``.docx`` exam paper from doc_intel question nodes
in a knowledge base: title + numbered question stems, with an optional
answer sheet appended at the end. Read-only with respect to the knowledge
base — nothing is written into the index; the document is streamed back as
an attachment (the same contract as ``question/paper-reorder/export``).

Data source: the active LlamaIndex docstore, where the doc_intel qa_split
pass tags question blocks with ``is_question=true`` (plus ``q_id`` /
``q_type`` / ``has_answer``) and answer blocks with ``is_answer=true`` and
the same ``q_id``. A requested id resolves by docstore node id first, then
by ``q_id`` metadata, so both the by-struct API output and raw ``q_id``
values work. Missing ids are skipped with a warning header instead of
aborting the whole paper (fail-open).
"""

from __future__ import annotations

from io import BytesIO
import logging
import re
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from deeptutor.multi_user.context import get_current_user
from deeptutor.services.config import PROJECT_ROOT, load_config_with_main

config = load_config_with_main("main.yaml", PROJECT_ROOT)
logger = logging.getLogger(__name__)

router = APIRouter()

DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
#: docx is a ZIP container — tests assert the ``PK`` magic number.
DOCX_MAX_QUESTION_CHARS = 2000

#: Leading question number ("1." / "3、" / "12)") left by qa_split in the
#: stem — the paper re-numbers questions, so the original is stripped.
_STEM_LEADING_NUM_RE = re.compile(r"^\s*\d{1,3}\s*[.、．)]\s*")

_TEACHER_ROLES = {"admin", "teacher"}


class ExamPaperAssembleRequest(BaseModel):
    """Body of ``POST /api/v1/exam-paper/assemble``."""

    kb_name: str = Field(min_length=1)
    q_ids: list[str] = Field(min_length=1, max_length=500)
    title: str | None = None
    include_answers: bool = False


def _load_docstore(kb_name: str):
    """Load the active LlamaIndex docstore for read access, or ``None``."""
    from deeptutor.multi_user.knowledge_access import (
        manager_for_resource,
        resolve_kb,
    )

    resource = resolve_kb(kb_name)
    manager = manager_for_resource(resource)
    storage_dir = manager.get_rag_storage_path(resource.name)
    docstore_path = storage_dir / "docstore.json"
    if not docstore_path.is_file():
        return resource.name, None

    from llama_index.core.storage.docstore import SimpleDocumentStore

    return resource.name, SimpleDocumentStore.from_persist_dir(str(storage_dir))


def _node_text(node) -> str:
    """Read display text across the LlamaIndex node API variants we support."""
    get_content = getattr(node, "get_content", None)
    if callable(get_content):
        return str(get_content() or "")
    return str(getattr(node, "text", "") or "")


def _strip_leading_number(text: str) -> str:
    """Remove the qa_split leading question number so the paper can re-number."""
    return _STEM_LEADING_NUM_RE.sub("", text, count=1).strip()


def _node_metadata(node) -> dict[str, Any]:
    return dict(getattr(node, "metadata", {}) or {})


def _node_q_id(metadata: dict[str, Any]) -> str:
    return str(metadata.get("q_id") or "")


def _resolve_question_nodes(nodes: list[Any], q_ids: list[str]) -> tuple[list[dict], list[str]]:
    """Resolve requested ids to question payloads; return (found, missing).

    Each requested id is tried as a docstore node id first (the by-struct API
    output), then as a ``q_id`` metadata value. A matched node is only kept
    when it actually looks like a question (``is_question`` or a ``q_id``
    marker), so an answer/analysis node id can never be exported as a stem.
    """
    by_node_id = {str(getattr(node, "node_id", "")): node for node in nodes}
    by_q_id: dict[str, Any] = {}
    for node in nodes:
        metadata = _node_metadata(node)
        qid = _node_q_id(metadata)
        if qid and qid not in by_q_id:
            by_q_id[qid] = node

    found: list[dict] = []
    missing: list[str] = []
    seen: set[str] = set()
    for raw_id in q_ids:
        requested = str(raw_id).strip()
        if not requested:
            continue
        node = by_node_id.get(requested)
        if node is None:
            node = by_q_id.get(requested)
        if node is None:
            missing.append(requested)
            continue
        metadata = _node_metadata(node)
        qid = _node_q_id(metadata)
        if not (metadata.get("is_question") or qid):
            missing.append(requested)
            continue
        dedupe_key = str(getattr(node, "node_id", "")) or qid
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        found.append(
            {
                "node_id": str(getattr(node, "node_id", "")),
                "q_id": qid,
                "text": _strip_leading_number(_node_text(node))[
                    :DOCX_MAX_QUESTION_CHARS
                ],
                "question_type": str(metadata.get("q_type") or ""),
                "answer": "",
            }
        )
    return found, missing


def _build_answer_map(nodes: list[Any]) -> dict[str, str]:
    """Map ``q_id`` → answer text from answer/analysis blocks and inline fields.

    The qa_split pass tags the answer block with ``is_answer=true`` and the
    same ``q_id`` as its question. Some stores also embed the answer directly
    on the question node (``answer`` / ``correct_answer`` metadata) — that
    inline value wins over the standalone block.
    """
    answers: dict[str, str] = {}
    inline: dict[str, str] = {}
    for node in nodes:
        metadata = _node_metadata(node)
        qid = _node_q_id(metadata)
        if not qid:
            continue
        text = _node_text(node).strip()
        if metadata.get("is_answer") and text and qid not in answers:
            answers[qid] = text
        inline_value = str(metadata.get("answer") or metadata.get("correct_answer") or "").strip()
        if inline_value and qid not in inline:
            inline[qid] = inline_value
    # Inline answer wins over the standalone block; a question whose answer
    # lives only inline (no is_answer block) is still covered.
    return {
        qid: inline.get(qid, answers.get(qid, ""))
        for qid in set(answers) | set(inline)
    }


def _sanitize_filename(name: str, fallback: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    return cleaned[:80] or fallback


def _content_disposition(title: str) -> str:
    """Attachment header for a possibly non-ASCII title.

    ``filename=`` must stay latin-1 safe (Starlette encodes headers as
    latin-1), so the UTF-8 name rides along via RFC 5987 ``filename*=``.
    """
    base = _sanitize_filename(title, "exam_paper")
    ascii_fallback = re.sub(r"[^\x20-\x7e]", "_", base)[:80] or "exam_paper"
    return (
        f'attachment; filename="{ascii_fallback}.docx"; '
        f"filename*=UTF-8''{quote(base + '.docx')}"
    )


def _set_run_cjk(run) -> None:
    """Pin ASCII + East-Asian fonts on a run so Chinese stems render in Word."""
    from docx.oxml.ns import qn

    run.font.name = "Calibri"
    run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")


def build_exam_paper_docx(
    *,
    title: str,
    questions: list[dict],
    include_answers: bool,
) -> bytes:
    """Render the paper to a ``.docx`` byte stream (``PK`` ZIP container)."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Pt

    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)
    normal._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")

    def add_para(text: str, *, bold: bool = False, size: int = 11, align=None) -> None:
        paragraph = doc.add_paragraph()
        if align is not None:
            paragraph.alignment = align
        run = paragraph.add_run(text)
        run.bold = bold
        run.font.size = Pt(size)
        _set_run_cjk(run)
        return paragraph

    add_para(title or "试卷", bold=True, size=18, align=WD_ALIGN_PARAGRAPH.CENTER)

    # Group by question type into Chinese-exam major sections (review round 2:
    # teachers expect 一、选择题 / 二、填空题 / 三、解答题 with per-section
    # counts and continuous numbering; data already carries q_type).
    section_order = ["choice", "fill", "short", "open", ""]
    zh_names = {
        "choice": "一、选择题",
        "fill": "二、填空题",
        "short": "三、解答题",
        "open": "三、解答题",
        "": "四、其他",
    }
    groups: dict[str, list[dict]] = {}
    for question in questions:
        qtype = str(question.get("q_type") or "").strip().lower()
        key = qtype if qtype in zh_names else ""
        groups.setdefault(key, []).append(question)

    ordered = [k for k in section_order if k in groups]
    ordered += [k for k in groups if k not in section_order]

    add_para(f"共 {len(questions)} 题", size=10, align=WD_ALIGN_PARAGRAPH.CENTER)
    doc.add_paragraph()

    numbering = 0
    per_question_number: list[tuple[int, dict]] = []
    for key in ordered:
        bucket = groups[key]
        add_para(f"{zh_names.get(key, '四、其他')}（共 {len(bucket)} 题）", bold=True, size=12)
        doc.add_paragraph()
        for question in bucket:
            numbering += 1
            add_para(f"{numbering}. {question['text']}")
            per_question_number.append((numbering, question))
            doc.add_paragraph()

    if include_answers:
        doc.add_page_break()
        add_para("参考答案", bold=True, size=16, align=WD_ALIGN_PARAGRAPH.CENTER)
        doc.add_paragraph()
        for idx, question in per_question_number:
            answer = question.get("answer") or "（该题未收录答案）"
            add_para(f"{idx}. {answer}")
            doc.add_paragraph()

    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


@router.post("/assemble")
def assemble_exam_paper(body: ExamPaperAssembleRequest) -> Response:
    """Assemble selected questions into a downloadable exam paper docx."""
    if body.include_answers and get_current_user().role not in _TEACHER_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Teacher access is required for answer sheets",
        )

    resolved_name, docstore = _load_docstore(body.kb_name)
    if docstore is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Knowledge base '{body.kb_name}' has no index (docstore is empty); "
                "upload and process documents before assembling a paper."
            ),
        )
    nodes = list(docstore.docs.values())
    questions, missing = _resolve_question_nodes(nodes, body.q_ids)
    if not questions:
        detail = (
            f"No doc_intel question nodes matched in knowledge base "
            f"'{resolved_name}' for the requested ids: {', '.join(missing[:20])}"
        )
        raise HTTPException(status_code=404, detail=detail)

    answer_map = _build_answer_map(nodes)
    for question in questions:
        qid = question["q_id"]
        if qid:
            question["answer"] = answer_map.get(qid, "")

    title = (body.title or "").strip() or "试卷"
    content = build_exam_paper_docx(
        title=title,
        questions=questions,
        include_answers=body.include_answers,
    )

    filename = _sanitize_filename(title, "exam_paper") + ".docx"
    headers = {
        "Content-Disposition": _content_disposition(title),
    }
    if missing:
        headers["X-Missing-Ids"] = ",".join(missing[:50])
    return Response(content=content, media_type=DOCX_MEDIA_TYPE, headers=headers)
