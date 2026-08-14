"""Question/answer/analysis separation (题答分离).

Handles two layouts found in real MinerU products:

1. **Inline sandwich** (teacher editions, 教师版): each question is followed by
   its ``【答案】`` and ``【详解】`` blocks immediately — regex markers on the
   block stream.
2. **Answer section** (student edition + answer pages at the back): questions
   up front, a ``参考答案`` heading, then answers keyed by question number —
   cross-region backfill (answer_matcher methodology, ported from 管线测试).

Emits per-block roles: ``question`` / ``answer`` / ``analysis`` / ``body``,
question ids (``P<page>-N`` / section-local numbering), q_type, and the
answer↔question linkage table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

Q_NUM_RE = re.compile(r"^(\d{1,3})\s*[.、．)]\s*")
Q_NUM_PAREN_RE = re.compile(r"^[(（](\d{1,3})[)）]\s*")
ANSWER_MARK_RE = re.compile(r"^【?答案】?\s*[:：]?\s*(.*)$")
ANALYSIS_MARK_RE = re.compile(r"^【?详解|解析|点评|思路】?")
ANSWER_SECTION_RE = re.compile(r"^参考答案|答案速查|答案与解析|试题解析$")
Q_TYPE_RE = re.compile(r"选择题|填空题|解答题|单选|多选|判断题|计算题|证明题|应用题")

CHOICE_HINT_RE = re.compile(r"[ABCD][.、．]\s*\S|\(?[ABCD]\)?\s*$|（\s*）|\(\s*\)")


def _text_v1(block: dict) -> str:
    return re.sub(r"\s+", " ", block.get("text", "") or "").strip()


def _text_v2(block: dict) -> str:
    content = block.get("content") or {}
    parts = content.get("title_content") or content.get("paragraph_content") or []
    return re.sub(r"\s+", " ", "".join(p.get("content", "") for p in parts if isinstance(p, dict))).strip()


@dataclass
class QASplitResult:
    # Per-block role for content blocks ("" = plain body).
    roles: list[str] = field(default_factory=list)
    # question id -> dict(page, q_type, answer_block_idx, analysis_block_idx)
    questions: dict[str, dict[str, Any]] = field(default_factory=dict)
    # True when a trailing answer section was detected and backfilled.
    used_answer_section: bool = False

    def as_block_metadata(self, idx: int) -> dict[str, Any]:
        role = self.roles[idx] if idx < len(self.roles) else ""
        md: dict[str, Any] = {}
        if not role:
            return md
        if role == "question":
            md["is_question"] = True
            # find the owning question id
            for qid, q in self.questions.items():
                if q.get("q_block_idx") == idx:
                    md["q_id"] = qid
                    if q.get("q_type"):
                        md["q_type"] = q["q_type"]
                    if q.get("answer_block_idx") is not None:
                        md["has_answer"] = True
                    break
        elif role == "answer":
            md["is_answer"] = True
            for qid, q in self.questions.items():
                if q.get("answer_block_idx") == idx:
                    md["q_id"] = qid
                    break
        elif role == "analysis":
            md["is_analysis"] = True
        return md


def split_qa(blocks: list[dict], *, text_fn=_text_v1, page_key: str = "page_idx") -> QASplitResult:
    """Sequential scan with state machine: q → [answer] → [analysis] → next q."""
    result = QASplitResult()
    roles: list[str] = [""] * len(blocks)
    questions: dict[str, dict[str, Any]] = {}

    current_q: str | None = None
    current_page = 0
    section_counts: dict[str, int] = {}
    in_answer_section = False
    last_answer_section_q: str | None = None
    q_type_context = ""

    for idx, block in enumerate(blocks):
        page = block.get(page_key, current_page)
        if isinstance(page, int):
            current_page = page
        text = text_fn(block)

        if not text:
            roles[idx] = ""
            continue

        # Answer-section heading toggles trailing answer mode.
        if ANSWER_SECTION_RE.match(text) and len(text) < 20:
            in_answer_section = True
            roles[idx] = "body"
            continue

        # ── inside trailing answer section: key by question number ──
        if in_answer_section:
            m = Q_NUM_RE.match(text) or Q_NUM_PAREN_RE.match(text)
            if m:
                key = f"P{current_page}-{m.group(1)}"
                last_answer_section_q = key
                q = questions.setdefault(key, {"page": current_page, "num": m.group(1),
                                               "q_block_idx": None, "answer_block_idx": None,
                                               "analysis_block_idx": None, "q_type": ""})
                q["answer_block_idx"] = idx
                roles[idx] = "answer"
                result.used_answer_section = True
                continue
            if last_answer_section_q and (ANALYSIS_MARK_RE.match(text) or roles[idx - 1] in ("answer", "analysis") if idx else False):
                q = questions[last_answer_section_q]
                if q.get("analysis_block_idx") is None:
                    q["analysis_block_idx"] = idx
                roles[idx] = "analysis"
                continue
            roles[idx] = "body"
            continue

        # ── normal flow: section headers give q_type context ──
        tmatch = Q_TYPE_RE.search(text)
        if tmatch and len(text) < 80 and not Q_NUM_RE.match(text):
            q_type_context = tmatch.group(0)
            roles[idx] = "body"
            continue

        # Answer / analysis markers attach to the current question.
        am = ANSWER_MARK_RE.match(text)
        if am and current_q:
            q = questions[current_q]
            q["answer_block_idx"] = idx
            roles[idx] = "answer"
            # 答案+详解 merged in one block (seen in real samples).
            if ANALYSIS_MARK_RE.search(text):
                q["analysis_block_idx"] = idx
                roles[idx] = "answer"  # keep primary role; analysis noted
            continue
        if ANALYSIS_MARK_RE.match(text) and current_q:
            q = questions[current_q]
            if q.get("analysis_block_idx") is None:
                q["analysis_block_idx"] = idx
            roles[idx] = "analysis"
            continue

        # Question start?
        qm = Q_NUM_RE.match(text)
        if qm and len(text) > 6:
            num = qm.group(1)
            sec = section_counts.get(q_type_context, 0) + 1
            section_counts[q_type_context] = sec
            qid = f"P{current_page}-{num}"
            # De-dupe: page+num collisions fall back to sequential.
            if qid in questions:
                qid = f"P{current_page}-{num}-{sec}"
            current_q = qid
            questions[qid] = {"page": current_page, "num": num, "q_block_idx": idx,
                              "answer_block_idx": None, "analysis_block_idx": None,
                              "q_type": q_type_context or ("choice" if CHOICE_HINT_RE.search(text) else "")}
            roles[idx] = "question"
            continue

        # Continuation text right after a question (before answer marker)
        if current_q and questions[current_q].get("answer_block_idx") is None:
            roles[idx] = "question"  # multi-line stem
        else:
            roles[idx] = "body"

    result.roles = roles
    result.questions = questions
    return result
